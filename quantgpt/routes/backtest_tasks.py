"""Backtest submission, task CRUD, and SSE streaming routes."""

import asyncio
import json
import logging
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import GUEST_USER_ID, get_current_user, get_optional_user
from ..db import get_db
from ..expression_parser import parse_expression
from ..iteration import compute_factor_score
from ..llm_service import (
    call_deepseek as _call_deepseek,
)
from ..llm_service import (
    call_fix_expression as _call_fix_expression,
)
from ..llm_service import (
    call_interpret_factor as _call_interpret_factor,
)
from ..llm_service import get_llm_api_key
from ..llm_service import (
    looks_like_expression as _looks_like_expression,
)
from ..llm_service import (
    validate_parentheses as _validate_parentheses,
)
from ..market_data import MarketDataFetcher, fetch_benchmark_returns, get_universe
from ..models import Task as TaskModel
from ..models import User
from ..report import generate_report
from ..schemas import validate_benchmark_value as _validate_bench_fn
from ..schemas import validate_date_format as _validate_date_fn
from ..schemas import validate_universe_value as _validate_univ_fn
from ..task_executor import _run_backtest_in_process, get_executor
from ..task_store import (
    MAX_ACTIVE_TASKS,
    MAX_DATE_RANGE_YEARS,
    MAX_PROMPT_LENGTH,
    MAX_SSE_CONNECTIONS,
    SSE_TIMEOUT_SECONDS,
    CancelledException,
    active_task_count,
    check_cancelled,
    check_rate_limit,
    cleanup_reports,
    cleanup_tasks,
    persist_task_to_db,
    sanitize_task_response,
    tasks,
    tasks_lock,
)

logger = logging.getLogger(__name__)


def _ensure_utc(dt: datetime) -> datetime:
    """Attach UTC timezone to naive datetimes (SQLite drops tzinfo)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


router = APIRouter()


class AutoBacktestRequest(BaseModel):
    prompt: str = Field(..., description="自然语言描述", examples=["帮我测试一个20日动量因子"])
    universe: str = Field("hs300", description="股票池")
    start_date: str = Field("2023-01-01", description="起始日期 YYYY-MM-DD")
    end_date: str = Field("2025-12-31", description="结束日期 YYYY-MM-DD")
    n_groups: int = Field(5, description="分组数量", ge=2, le=20)
    holding_period: int = Field(5, description="持仓周期(交易日)", ge=1, le=60)
    benchmark: str = Field("hs300", description="基准指数")
    session_id: str | None = Field(None, description="关联会话 ID")
    neutralize_industry: bool = Field(True, description="行业中性化")
    neutralize_cap: bool = Field(True, description="市值中性化")
    universe_date: str | None = Field(None, description="股票池基准日期，用于子区间验证时固定股票池。为空时使用 start_date")
    rebalance_anchor: str | None = Field(None, description="换仓网格锚定日期，用于跨期比较时对齐换仓时间。为空时从数据起始日开始")

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("prompt 不能为空")
        if len(v) > MAX_PROMPT_LENGTH:
            raise ValueError(f"prompt 长度不能超过 {MAX_PROMPT_LENGTH} 字符")
        return v

    _validate_universe = field_validator("universe")(_validate_univ_fn)
    _validate_benchmark = field_validator("benchmark")(_validate_bench_fn)
    _validate_dates = field_validator("start_date", "end_date", "universe_date", "rebalance_anchor")(_validate_date_fn)


def _run_backtest_task(task_id: str, req: AutoBacktestRequest, user_id: str):
    task = tasks.get(task_id)
    if not task:
        return

    report_filename = None
    try:
        start = datetime.strptime(req.start_date, "%Y-%m-%d")
        end = datetime.strptime(req.end_date, "%Y-%m-%d")
        if start >= end:
            task["status"] = "failed"
            task["error"] = "开始日期必须早于结束日期"
            return
        if (end - start).days > MAX_DATE_RANGE_YEARS * 365:
            task["status"] = "failed"
            task["error"] = f"日期范围不能超过 {MAX_DATE_RANGE_YEARS} 年"
            return

        task["status"] = "generating_expression"
        expression = None
        user_text = req.prompt.strip()
        if _looks_like_expression(user_text):
            try:
                from ..fundamental_data import ALL_FUNDAMENTAL_NAMES as _FUND_NAMES
                _test_dummy = pd.DataFrame({
                    "open": [1.0, 2.0, 3.0], "high": [1.1, 2.1, 3.1],
                    "low": [0.9, 1.9, 2.9], "close": [1.0, 2.0, 3.0],
                    "volume": [100, 200, 300], "amount": [100, 400, 900],
                    "pct_change": [0, 100, 50],
                    "trade_date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
                    **{name: [1.0, 1.1, 1.2] for name in _FUND_NAMES},
                })
                parse_expression(user_text)(_test_dummy)
                expression = user_text
                logger.info(f"[{task_id}] user input is a valid expression, using directly: {expression}")
            except Exception:
                pass

        if expression is None:
            if not get_llm_api_key():
                task["status"] = "failed"
                task["error"] = (
                    "未配置 LLM API Key，无法解析自然语言。"
                    "请直接输入因子表达式（如 rank(close/ts_mean(close,20))），"
                    "或设置 LLM_API_KEY 环境变量启用自然语言输入。"
                )
                return
            expression = _call_deepseek(req.prompt)
        task["expression"] = expression
        logger.info(f"[{task_id}] expression: {expression}")

        task["status"] = "validating"
        from ..fundamental_data import ALL_FUNDAMENTAL_NAMES as _FUND_NAMES2
        dummy = pd.DataFrame({
            "open": [1.0, 2.0, 3.0], "high": [1.1, 2.1, 3.1],
            "low": [0.9, 1.9, 2.9], "close": [1.0, 2.0, 3.0],
            "volume": [100, 200, 300], "amount": [100, 400, 900],
            "pct_change": [0, 100, 50],
            "trade_date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
            **{name: [1.0, 1.1, 1.2] for name in _FUND_NAMES2},
        })

        paren_err = _validate_parentheses(expression)
        if paren_err:
            if get_llm_api_key():
                logger.warning(f"[{task_id}] parentheses error, attempting fix: {paren_err}")
                expression = _call_fix_expression(expression, paren_err, req.prompt)
                task["expression"] = expression
            else:
                task["status"] = "failed"
                task["error"] = f"表达式语法错误: {paren_err}"
                return

        try:
            func_ = parse_expression(expression)
            func_(dummy)
        except Exception as e:
            if get_llm_api_key():
                logger.warning(f"[{task_id}] validation failed, attempting fix: {e}")
                try:
                    fixed = _call_fix_expression(expression, str(e), req.prompt)
                    func_ = parse_expression(fixed)
                    func_(dummy)
                    expression = fixed
                    task["expression"] = expression
                    logger.info(f"[{task_id}] expression fixed: {expression}")
                except Exception as e2:
                    task["status"] = "failed"
                    task["error"] = f"因子表达式无效: {e2}"
                    return
            else:
                task["status"] = "failed"
                task["error"] = f"因子表达式无效: {e}"
                return

        check_cancelled(task_id)
        task["status"] = "fetching_data"
        universe_resolve_date = req.universe_date or req.start_date
        stock_codes = get_universe(req.universe, date=universe_resolve_date)
        fetcher = MarketDataFetcher()
        market_df = fetcher.fetch_stocks(stock_codes, req.start_date, req.end_date)
        if market_df is None or len(market_df) == 0:
            task["status"] = "failed"
            task["error"] = "未获取到行情数据，请检查日期范围"
            return

        from ..fundamental_data import detect_fundamental_vars, enrich_market_data
        fund_vars = detect_fundamental_vars(expression)
        if fund_vars:
            check_cancelled(task_id)
            task["status"] = "fetching_fundamentals"
            logger.info(f"[{task_id}] fetching fundamentals for vars: {fund_vars}")
            market_df = enrich_market_data(market_df, fund_vars, stock_codes, req.start_date, req.end_date)

        check_cancelled(task_id)
        task["status"] = "backtesting"
        executor = get_executor()
        future = executor.submit_cpu_work(
            _run_backtest_in_process,
            market_df, expression, req.n_groups, req.holding_period,
            neutralize_industry=req.neutralize_industry,
            neutralize_cap=req.neutralize_cap,
            trading_days_per_year=252,
            rebalance_anchor=req.rebalance_anchor,
        )
        while True:
            try:
                result = future.result(timeout=2)
                break
            except TimeoutError:
                check_cancelled(task_id)

        check_cancelled(task_id)
        anti_overfit_result = None
        factor_df = result.get("_factor_df")
        if factor_df is not None and len(factor_df) > 100:
            task["status"] = "analyzing"
            try:
                from ..anti_overfit import run_anti_overfit
                anti_overfit_result = run_anti_overfit(factor_df, req.holding_period)
            except Exception as e:
                logger.warning(f"[{task_id}] anti-overfit analysis failed: {e}")

        adversarial_result = None
        if factor_df is not None and len(factor_df) > 100:
            try:
                from ..adversarial_validator import run_adversarial_validation
                adversarial_result = run_adversarial_validation(factor_df, req.holding_period)
            except Exception as e:
                logger.warning(f"[{task_id}] adversarial validation failed: {e}")

        check_cancelled(task_id)
        task["status"] = "generating_report"
        bm_returns = None
        try:
            bm_returns = fetch_benchmark_returns(req.benchmark, req.start_date, req.end_date)
        except Exception:
            logger.warning(f"[{task_id}] benchmark fetch failed")

        user_report_dir = Path(__file__).resolve().parent.parent.parent / "reports" / user_id
        user_report_dir.mkdir(parents=True, exist_ok=True)

        report_result = generate_report(
            result["strategy_returns"],
            benchmark_returns=bm_returns,
            title="Factor Top-Group Backtest",
            output_dir=str(user_report_dir),
            periods_per_year=252,
        )
        report_filename = Path(report_result["report_path"]).name

        interpretation = {}
        try:
            interpretation = _call_interpret_factor(
                expression=expression,
                prompt=req.prompt,
                metrics=report_result["metrics"],
                backtest_summary={
                    "ic_mean": result.get("ic_mean", 0),
                    "rank_ic_mean": result.get("rank_ic_mean", 0),
                    "monotonicity_score": result["monotonicity_score"],
                    "turnover": result.get("turnover", 0),
                },
            )
        except Exception as e:
            logger.warning(f"[{task_id}] interpretation failed: {e}")

        ao_score_val = anti_overfit_result.get("score") if anti_overfit_result else None
        factor_df = result.get("_factor_df")
        data_days = len(factor_df["trade_date"].unique()) if factor_df is not None else 0
        scoring = compute_factor_score(
            backtest_summary={
                "long_short_sharpe": result["long_short_sharpe"],
                "monotonicity_score": result["monotonicity_score"],
                "spread": result["spread"],
                "ic_mean": result.get("ic_mean", 0),
                "rank_ic_mean": result.get("rank_ic_mean", 0),
                "ic_ir": result.get("ic_ir", 0),
                "ic_win_rate": result.get("ic_win_rate", 0),
                "turnover": result.get("turnover", 0),
            },
            report_metrics=report_result["metrics"],
            anti_overfit_score=ao_score_val,
            data_days=data_days,
        )
        interpretation["rating"] = scoring["grade"]
        interpretation["rating_reason"] = f"综合评分 {scoring['score']}/100"

        cloud_validation = None
        if scoring["grade"] == "A" and scoring.get("cloud_predicted_pass") and factor_df is not None:
            task["status"] = "uploading_cloud"
            try:
                from ..cloud_client import auto_upload_to_cloud
                cloud_validation = auto_upload_to_cloud(
                    expression=expression,
                    universe=req.universe,
                    factor_df=result["_factor_df"],
                    claimed_ic_mean=result.get("ic_mean"),
                    claimed_ic_ir=result.get("ic_ir"),
                )
            except Exception as e:
                logger.warning(f"[{task_id}] Cloud auto-upload failed: {e}")

        task["status"] = "completed"

        nav_series = []
        try:
            strat_ret = result["strategy_returns"]
            if hasattr(strat_ret, "index") and len(strat_ret) > 0:
                cum = (1 + strat_ret).cumprod()
                step = max(1, len(cum) // 50)
                sampled = cum.iloc[::step]
                if sampled.index[-1] != cum.index[-1]:
                    sampled = pd.concat([sampled, cum.iloc[[-1]]])
                nav_series = [
                    {"date": d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d), "value": round(float(v), 4)}
                    for d, v in sampled.items()
                ]
        except Exception:
            pass

        task["result"] = {
            "report_url": f"/api/v1/reports/{report_filename}",
            "metrics": report_result["metrics"],
            "backtest_summary": {
                "long_short_sharpe": result["long_short_sharpe"],
                "long_short_annual": result.get("long_short_annual", 0),
                "top_group_sharpe": result.get("top_group_sharpe", 0),
                "monotonicity_score": result["monotonicity_score"],
                "spread": result["spread"],
                "group_returns": result["group_returns"],
                "ic_mean": result.get("ic_mean", 0),
                "rank_ic_mean": result.get("rank_ic_mean", 0),
                "ic_ir": result.get("ic_ir", 0),
                "ic_win_rate": result.get("ic_win_rate", 0),
                "turnover": result.get("turnover", 0),
                "wq_fitness": result.get("wq_fitness", 0),
                "cost_adjusted": result.get("cost_adjusted", False),
                "cost_rate": result.get("cost_rate", 0),
                "total_cost_drag": result.get("total_cost_drag", 0),
            },
            "wq_brain": result.get("wq_brain", {}),
            "anti_overfit": anti_overfit_result,
            "adversarial": adversarial_result,
            "scoring": scoring,
            "interpretation": interpretation,
            "cloud_validation": cloud_validation,
            "stock_factor_data": result.get("_stock_factor_data"),
            "nav_series": nav_series,
            "params": {
                "expression": expression,
                "universe": req.universe,
                "universe_date": universe_resolve_date,
                "rebalance_anchor": req.rebalance_anchor,
                "start_date": req.start_date,
                "end_date": req.end_date,
                "n_groups": req.n_groups,
                "holding_period": req.holding_period,
                "benchmark": req.benchmark,
                "stock_count": len(stock_codes),
            },
            "llm": {
                "prompt": req.prompt,
                "generated_expression": expression,
            },
        }
        logger.info(f"[{task_id}] completed")
        cleanup_reports(user_id)

    except CancelledException:
        logger.info(f"[{task_id}] cancelled by user")
        task["status"] = "cancelled"
    except Exception:
        logger.error(f"[{task_id}] failed: {traceback.format_exc()}")
        task["status"] = "failed"
        task["error"] = "回测过程中发生内部错误，请稍后重试"
    finally:
        if "completed_at" not in task:
            task["completed_at"] = time.time()
        if not task.get("is_guest"):
            try:
                persist_task_to_db(task_id, user_id, task, report_filename)
            except Exception as e:
                logger.error(f"[{task_id}] DB persist error: {e}")


@router.get("/api/v1/health", summary="健康检查")
def health():
    """检查服务状态，返回当前活跃任务数和总任务数。不需要认证。"""
    return {
        "status": "ok",
        "active_tasks": active_task_count(),
        "total_tasks": len(tasks),
    }


@router.post("/api/v1/tasks/{task_id}/cancel", summary="取消回测任务")
async def cancel_task(
    task_id: str,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    user_id = str(user.id) if user else GUEST_USER_ID

    task = tasks.get(task_id)
    if task:
        if task.get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="无权操作此任务")
        if task["status"] in ("completed", "failed", "cancelled", "iteration_completed"):
            raise HTTPException(status_code=400, detail="任务已结束，无法取消")
        with tasks_lock:
            task["cancelled"] = True
            task["status"] = "cancelled"
        logger.info(f"[{task_id}] cancel requested by user")
        return {"task_id": task_id, "status": "cancelled"}

    result = await db.execute(
        select(TaskModel).where(TaskModel.id == task_id, TaskModel.user_id == user.id)
    )
    db_task = result.scalar_one_or_none()
    if not db_task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if db_task.status in ("completed", "failed", "cancelled", "iteration_completed"):
        raise HTTPException(status_code=400, detail="任务已结束，无法取消")
    db_task.status = "cancelled"
    db_task.error = "用户手动取消"
    await db.commit()
    logger.info(f"[{task_id}] db task cancelled by user")
    return {"task_id": task_id, "status": "cancelled"}


@router.post("/api/v1/auto_backtest", status_code=202, summary="提交因子回测任务")
async def auto_backtest(
    req: AutoBacktestRequest,
    request: Request,
    user: User | None = Depends(get_optional_user),
):
    """提交异步回测任务。支持自然语言 prompt 或直接因子表达式。返回 task_id，用 GET /api/v1/tasks/{task_id} 轮询结果。"""
    client_ip = request.client.host if request.client else "unknown"

    if not check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")

    if active_task_count() >= MAX_ACTIVE_TASKS:
        raise HTTPException(status_code=503, detail="当前回测任务已满，请稍后再试")

    cleanup_tasks()

    is_guest = user is None
    task_id = uuid.uuid4().hex[:12]
    user_id = str(user.id) if user else GUEST_USER_ID
    session_id = req.session_id

    if is_guest:
        req.universe = "small_scale"
        session_id = None

    with tasks_lock:
        tasks[task_id] = {
            "task_id": task_id,
            "user_id": user_id,
            "session_id": session_id,
            "status": "pending",
            "cancelled": False,
            "params": req.model_dump(exclude={"session_id"}),
            "created_at": time.time(),
            "is_guest": is_guest,
        }
    logger.info(f"task {task_id} created for {'guest' if is_guest else user.email}")

    thread = threading.Thread(
        target=_run_backtest_task, args=(task_id, req, user_id), daemon=True
    )
    thread.start()

    return {"task_id": task_id, "status": "pending"}


@router.get("/api/v1/tasks/stats", summary="任务统计")
async def task_stats(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user_id = user.id

    db_total = (await db.execute(select(func.count()).select_from(TaskModel).where(TaskModel.user_id == user_id))).scalar() or 0
    completed = (await db.execute(select(func.count()).select_from(TaskModel).where(TaskModel.user_id == user_id, TaskModel.status == "completed"))).scalar() or 0
    failed = (await db.execute(select(func.count()).select_from(TaskModel).where(TaskModel.user_id == user_id, TaskModel.status == "failed"))).scalar() or 0

    uid_str = str(user_id)
    with tasks_lock:
        memory_ids = {t["task_id"] for t in tasks.values() if t.get("user_id") == uid_str}
    running = len(memory_ids)
    if memory_ids:
        db_overlap = (await db.execute(
            select(func.count()).select_from(TaskModel).where(TaskModel.id.in_(memory_ids))
        )).scalar() or 0
    else:
        db_overlap = 0
    total = db_total + running - db_overlap

    rating_dist: dict[str, int] = {}
    rows = (await db.execute(
        select(TaskModel.result).where(TaskModel.user_id == user_id, TaskModel.status == "completed")
    )).scalars().all()
    for r in rows:
        if isinstance(r, dict):
            rating = r.get("interpretation", {}).get("rating") or r.get("backtest_summary", {}).get("wq_rating", "")
            if rating:
                rating_dist[rating] = rating_dist.get(rating, 0) + 1

    return {
        "total": total,
        "completed": completed,
        "failed": failed,
        "running": running - db_overlap,
        "success_rate": round(completed / total * 100, 1) if total else 0,
        "rating_distribution": rating_dist,
    }


@router.get("/api/v1/tasks", summary="查询任务列表")
async def list_tasks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session_id: str | None = Query(None, description="按会话 ID 过滤"),
    task_type: str | None = Query(None, description="按任务类型过滤: backtest / iteration / composite"),
    status: str | None = Query(None, description="按状态过滤: completed / failed / pending"),
    rating: str | None = Query(None, description="按评级过滤: A / B / C / D"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user_id = str(user.id)
    offset = (page - 1) * page_size

    memory_tasks = []
    with tasks_lock:
        for t in tasks.values():
            if t.get("user_id") == user_id:
                if session_id is not None and t.get("session_id") != session_id:
                    continue
                if task_type is not None and t.get("task_type") != task_type:
                    continue
                if status is not None:
                    t_status = t.get("status", "")
                    if status == "running":
                        if t_status in ("completed", "failed"):
                            continue
                    elif t_status != status:
                        continue
                if rating is not None:
                    r = t.get("result", {}) or {}
                    t_rating = (r.get("interpretation", {}) or {}).get("rating") or (r.get("backtest_summary", {}) or {}).get("wq_rating", "")
                    if t_rating != rating:
                        continue
                safe = {k: v for k, v in t.items() if k not in ("user_id",)}
                memory_tasks.append(safe)

    query = select(TaskModel).where(TaskModel.user_id == user.id)
    if session_id is not None:
        import uuid as _uuid
        try:
            query = query.where(TaskModel.session_id == _uuid.UUID(session_id))
        except ValueError:
            pass
    if task_type is not None:
        query = query.where(TaskModel.task_type == task_type)
    if status is not None:
        query = query.where(TaskModel.status == status)
    query = query.order_by(desc(TaskModel.created_at))
    result = await db.execute(query)
    db_tasks = result.scalars().all()

    memory_ids = {t["task_id"] for t in memory_tasks}
    merged = list(memory_tasks)
    for dt in db_tasks:
        if dt.id not in memory_ids:
            dur = None
            if dt.status in ("completed", "failed", "cancelled", "iteration_completed") and dt.created_at and dt.updated_at:
                dur = round((dt.updated_at - dt.created_at).total_seconds(), 1)
            task_dict = {
                "task_id": dt.id,
                "status": dt.status,
                "task_type": dt.task_type,
                "session_id": str(dt.session_id) if dt.session_id else None,
                "params": dt.params,
                "expression": dt.expression,
                "result": dt.result,
                "error": dt.error,
                "created_at": _ensure_utc(dt.created_at).isoformat() if dt.created_at else None,
                "completed_at": _ensure_utc(dt.updated_at).isoformat() if dt.status in ("completed", "failed", "cancelled", "iteration_completed") and dt.updated_at else None,
                "duration_seconds": dur,
            }
            if rating is not None:
                r = task_dict.get("result", {}) or {}
                t_rating = (r.get("interpretation", {}) or {}).get("rating") or (r.get("backtest_summary", {}) or {}).get("wq_rating", "")
                if t_rating != rating:
                    continue
            merged.append(task_dict)

    def _sort_key(t: dict) -> float:
        ca = t.get("created_at")
        if isinstance(ca, (int, float)):
            return ca
        if isinstance(ca, str):
            try:
                return datetime.fromisoformat(ca).timestamp()
            except Exception:
                return 0
        return 0

    merged.sort(key=_sort_key, reverse=True)
    total = len(merged)
    merged = merged[offset:offset + page_size]
    return {"tasks": [sanitize_task_response(t) for t in merged], "page": page, "page_size": page_size, "total": total}


@router.get("/api/v1/tasks/{task_id}", summary="查询任务状态和结果")
async def get_task(
    task_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """返回任务当前状态。status=completed 时 result 字段包含回测指标（Sharpe、IC、Fitness 等）。回测是异步的，提交后需轮询此端点直到 status 变为 completed 或 failed。"""
    user_id = str(user.id)

    task = tasks.get(task_id)
    if task:
        if task.get("user_id") != user_id:
            raise HTTPException(status_code=404, detail="Task not found")
        safe = {k: v for k, v in task.items() if k not in ("created_at", "user_id")}
        return sanitize_task_response(safe)

    result = await db.execute(
        select(TaskModel).where(TaskModel.id == task_id, TaskModel.user_id == user.id)
    )
    db_task = result.scalar_one_or_none()
    if not db_task:
        raise HTTPException(status_code=404, detail="Task not found")

    resp = {
        "task_id": db_task.id,
        "status": db_task.status,
        "task_type": db_task.task_type,
        "params": db_task.params,
        "expression": db_task.expression,
        "result": db_task.result,
        "error": db_task.error,
    }
    if db_task.status == "iteration_completed" and isinstance(db_task.result, dict):
        resp["candidates"] = db_task.result.get("candidates", [])
        resp["candidates_done"] = len(resp["candidates"])
        resp["candidates_total"] = len(resp["candidates"])
        resp["task_type"] = "iteration"
        resp["parent_task_id"] = db_task.result.get("parent_task_id")
    return sanitize_task_response(resp)


@router.post("/api/v1/tasks/{task_id}/sse-ticket")
async def create_ticket(task_id: str):
    task = tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"ticket": "noop"}


@router.get("/api/v1/tasks/{task_id}/stream")
async def stream_task(task_id: str, request: Request):
    import quantgpt.task_store as _ts

    task = tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    with _ts.sse_lock:
        if _ts.active_sse_count >= MAX_SSE_CONNECTIONS:
            raise HTTPException(status_code=503, detail="SSE 连接数已满")
        _ts.active_sse_count += 1

    async def event_generator():
        try:
            last_status = None
            last_candidates_done = -1
            deadline = time.monotonic() + SSE_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                task = tasks.get(task_id)
                if not task:
                    yield f"event: error\ndata: {json.dumps({'error': 'Task not found'})}\n\n"
                    return

                current_status = task.get("status")
                current_candidates_done = task.get("candidates_done", -1)
                if current_status != last_status or current_candidates_done != last_candidates_done:
                    last_status = current_status
                    last_candidates_done = current_candidates_done
                    safe = {k: v for k, v in task.items() if k not in ("created_at", "user_id")}
                    safe = sanitize_task_response(safe)
                    payload = json.dumps(safe, ensure_ascii=False, default=str)
                    yield f"event: update\ndata: {payload}\n\n"

                    if current_status in ("completed", "failed", "iteration_completed"):
                        yield f"event: done\ndata: {json.dumps({'status': current_status})}\n\n"
                        return

                await asyncio.sleep(0.5)

            yield f"event: error\ndata: {json.dumps({'error': 'Stream timeout'})}\n\n"
        finally:
            with _ts.sse_lock:
                _ts.active_sse_count = max(0, _ts.active_sse_count - 1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
