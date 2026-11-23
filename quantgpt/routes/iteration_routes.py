"""Iteration optimization routes and background worker."""

import asyncio
import logging
import threading
import time
import traceback
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..db import get_db
from ..iteration import compute_factor_score, generate_iteration_candidates
from ..market_data import MarketDataFetcher, get_universe
from ..models import Task as TaskModel
from ..models import User
from ..task_store import (
    MAX_ACTIVE_TASKS,
    REPORT_DIR,
    SAFE_FILENAME_RE,
    active_task_count,
    check_rate_limit,
    cleanup_tasks,
    main_loop,
    persist_report_to_db,
    persist_task_to_db,
    tasks,
    tasks_lock,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class IterateRequest(BaseModel):
    n_candidates: int = Field(5, ge=1, le=10, description="候选因子数量")
    run_rolling_validation: bool = Field(False, description="是否运行滚动验证")
    direction: str | None = Field(None, description="迭代方向提示，如'加入量价信息'、'增加低波暴露'")


class SelectCandidateRequest(BaseModel):
    candidate_index: int = Field(..., ge=0, description="候选因子索引")


def _run_iteration_task(task_id: str, parent_task_id: str, user_id: str, n_candidates: int, direction: str | None = None):
    task = tasks.get(task_id)
    if not task:
        return

    if not task.get("is_guest"):
        try:
            persist_task_to_db(task_id, user_id, task)
        except Exception:
            pass

    try:
        parent_task = tasks.get(parent_task_id)
        if parent_task and parent_task.get("status") == "completed":
            parent_result = parent_task.get("result", {})
            parent_expression = parent_task.get("expression", "")
            parent_params = parent_result.get("params", {})
        else:
            async def _fetch_parent():
                from ..db import _get_session_factory
                factory = _get_session_factory()
                async with factory() as session:
                    r = await session.execute(
                        select(TaskModel).where(TaskModel.id == parent_task_id)
                    )
                    return r.scalar_one_or_none()

            db_parent = None
            _loop = main_loop
            if _loop and _loop.is_running():
                future = asyncio.run_coroutine_threadsafe(_fetch_parent(), _loop)
                try:
                    db_parent = future.result(timeout=10)
                except Exception as e:
                    logger.error(f"[{task_id}] fetch parent from DB failed: {e}")

            if not db_parent or db_parent.status != "completed":
                task["status"] = "failed"
                task["error"] = "父任务未完成或不存在"
                return

            parent_result = db_parent.result or {}
            parent_expression = db_parent.expression or ""
            parent_params = parent_result.get("params", {})

        if not parent_expression:
            task["status"] = "failed"
            task["error"] = "父任务缺少表达式"
            return

        task["status"] = "iterating"
        task["expression"] = parent_expression

        stock_codes = get_universe(
            parent_params.get("universe", "hs300"),
            date=parent_params.get("start_date", "2023-01-01"),
        )
        fetcher = MarketDataFetcher()
        market_df = fetcher.fetch_stocks(
            stock_codes,
            parent_params.get("start_date", "2023-01-01"),
            parent_params.get("end_date", "2025-12-31"),
        )
        if market_df is None or len(market_df) == 0:
            task["status"] = "failed"
            task["error"] = "无法获取行情数据"
            return

        from ..fundamental_data import detect_fundamental_vars, enrich_market_data
        fund_vars = detect_fundamental_vars(parent_expression)
        if fund_vars:
            logger.info(f"[{task_id}] iteration: enriching with fundamentals: {fund_vars}")
            sd = parent_params.get("start_date", "2023-01-01")
            ed = parent_params.get("end_date", "2025-12-31")
            market_df = enrich_market_data(market_df, fund_vars, stock_codes, sd, ed)

        parent_backtest_summary = parent_result.get("backtest_summary", {})
        parent_report_metrics = parent_result.get("metrics", {})
        parent_scoring = compute_factor_score(parent_backtest_summary, parent_report_metrics)

        parent_metrics = {
            "backtest_summary": parent_backtest_summary,
            "report_metrics": parent_report_metrics,
        }

        def on_progress(done_count, candidate_result):
            task["candidates_done"] = done_count
            if candidate_result.get("status") == "success":
                task["candidates"].append(candidate_result)
                report_filename = candidate_result.get("report_filename")
                if report_filename:
                    try:
                        persist_report_to_db(task_id, user_id, report_filename)
                    except Exception as e:
                        logger.error(f"[{task_id}] report persist error: {e}")
            else:
                task["candidates"].append(candidate_result)

        candidates = generate_iteration_candidates(
            parent_expression=parent_expression,
            parent_metrics=parent_metrics,
            parent_score=parent_scoring["score"],
            parent_grade=parent_scoring["grade"],
            params=parent_params,
            market_df=market_df,
            user_id=user_id,
            n_candidates=n_candidates,
            max_concurrent=50,
            on_progress=on_progress,
            task_id=task_id,
            direction=direction,
        )

        task["candidates"] = candidates
        task["candidates_done"] = len(candidates)
        task["status"] = "iteration_completed"
        task["result"] = {
            "parent_task_id": parent_task_id,
            "parent_expression": parent_expression,
            "parent_score": parent_scoring["score"],
            "parent_grade": parent_scoring["grade"],
            "candidates": candidates,
        }
        logger.info(f"[{task_id}] iteration completed: {len(candidates)} candidates")

    except Exception as e:
        logger.error(f"[{task_id}] iteration failed: {traceback.format_exc()}")
        task["status"] = "failed"
        task["error"] = f"迭代过程中发生错误: {str(e)}"
    finally:
        if "completed_at" not in task:
            import time as _time
            task["completed_at"] = _time.time()
        if not task.get("is_guest"):
            try:
                persist_task_to_db(task_id, user_id, task)
            except Exception as e:
                logger.error(f"[{task_id}] DB persist error: {e}")


@router.post("/api/v1/tasks/{task_id}/iterate", status_code=202, summary="提交迭代优化任务")
async def iterate_task(
    task_id: str,
    req: IterateRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    client_ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")

    user_id = str(user.id)

    parent_task = tasks.get(task_id)
    if parent_task:
        if parent_task.get("user_id") != user_id:
            raise HTTPException(status_code=404, detail="Task not found")
        if parent_task.get("status") != "completed":
            raise HTTPException(status_code=400, detail="只能对已完成的任务进行迭代优化")
        parent_params = parent_task.get("result", {}).get("params", {})
        parent_expression = parent_task.get("expression")
    else:
        result = await db.execute(
            select(TaskModel).where(TaskModel.id == task_id, TaskModel.user_id == user.id)
        )
        db_task = result.scalar_one_or_none()
        if not db_task:
            raise HTTPException(status_code=404, detail="Task not found")
        if db_task.status != "completed":
            raise HTTPException(status_code=400, detail="只能对已完成的任务进行迭代优化")
        parent_params = (db_task.result or {}).get("params", {})
        parent_expression = db_task.expression

    if active_task_count() >= MAX_ACTIVE_TASKS:
        raise HTTPException(status_code=503, detail="当前任务已满，请稍后再试")

    cleanup_tasks()

    iter_task_id = uuid.uuid4().hex[:12]
    with tasks_lock:
        tasks[iter_task_id] = {
            "task_id": iter_task_id,
            "user_id": user_id,
            "status": "pending",
            "task_type": "iteration",
            "parent_task_id": task_id,
            "params": {
                **parent_params,
                "parent_task_id": task_id,
                "n_candidates": req.n_candidates,
            },
            "expression": parent_expression,
            "candidates": [],
            "candidates_done": 0,
            "candidates_total": req.n_candidates,
            "created_at": time.time(),
        }
    logger.info(f"iteration task {iter_task_id} created for parent {task_id}")

    thread = threading.Thread(
        target=_run_iteration_task,
        args=(iter_task_id, task_id, user_id, req.n_candidates, req.direction),
        daemon=True,
    )
    thread.start()

    return {"task_id": iter_task_id, "status": "pending"}


@router.post("/api/v1/tasks/{task_id}/select_candidate", summary="选择迭代候选因子")
async def select_candidate(
    task_id: str,
    req: SelectCandidateRequest,
    user: User = Depends(get_current_user),
):
    user_id = str(user.id)

    task = tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.get("user_id") != user_id:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.get("status") != "iteration_completed":
        raise HTTPException(status_code=400, detail="迭代任务尚未完成")

    candidates = task.get("candidates", [])
    if req.candidate_index >= len(candidates):
        raise HTTPException(status_code=400, detail="候选索引超出范围")

    candidate = candidates[req.candidate_index]
    if candidate.get("status") != "success":
        raise HTTPException(status_code=400, detail="该候选因子回测失败，无法选择")

    task["selected_candidate_index"] = req.candidate_index

    return {
        "task_id": task_id,
        "selected_index": req.candidate_index,
        "expression": candidate.get("expression"),
        "score": candidate.get("score"),
        "grade": candidate.get("grade"),
        "report_url": candidate.get("report_url"),
        "report_metrics": candidate.get("report_metrics"),
        "backtest_summary": candidate.get("backtest_summary"),
    }


@router.get("/api/v1/reports/{filename}", summary="下载 HTML 回测报告")
async def get_report(
    filename: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not SAFE_FILENAME_RE.match(filename):
        raise HTTPException(status_code=400, detail="Invalid filename")

    user_id = str(user.id)
    user_report_dir = REPORT_DIR / user_id
    file_path = (user_report_dir / filename).resolve()

    if not file_path.is_relative_to(user_report_dir.resolve()):
        raise HTTPException(status_code=400, detail="Invalid path")

    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Report not found")

    return FileResponse(str(file_path), media_type="text/html")
