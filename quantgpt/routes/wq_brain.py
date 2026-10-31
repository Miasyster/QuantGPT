"""WQ BRAIN API routes — submit expressions to WorldQuant BRAIN for real simulation."""

import logging
import threading
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..db import get_db
from ..models import User
from ..task_store import (
    MAX_ACTIVE_TASKS,
    active_task_count,
    check_rate_limit,
    persist_task_to_db,
    tasks,
    tasks_lock,
)
from ..wq_brain_client import SUBMIT_THRESHOLDS, configured_accounts, get_client, is_configured
from ..wq_brain_service import fitness_to_grade, run_list_alphas, run_single_simulation, safe_float

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/wq-brain", tags=["wq_brain"])


_safe_float = safe_float
_fitness_to_grade = fitness_to_grade


class WQBrainSubmitRequest(BaseModel):
    expression: str = Field(..., description="FASTEXPR factor expression")
    tag: str = Field(..., min_length=1, max_length=100, description="Submitter tag (e.g. 'agent-lowcorr-0506')")
    region: str = Field("USA", description="Market region")
    universe: str = Field("TOP3000", description="WQ Universe")
    delay: int = Field(1, ge=0, le=1, description="Signal delay")
    decay: int = Field(0, ge=0, le=20, description="Alpha decay")
    neutralization: str = Field("SUBINDUSTRY", description="Neutralization method")
    truncation: float = Field(0.08, ge=0, le=0.5, description="Weight truncation")
    auto_submit: bool = Field(False, description="Auto-submit if checks pass")
    account: str = Field("primary", description="WQ account: 'primary' or 'alt'")
    session_id: str | None = Field(None, description="Session ID")


def _run_wq_brain_task(task_id: str, req: WQBrainSubmitRequest, user_id: str):
    task = tasks.get(task_id)
    if not task:
        return

    try:
        account = req.account if req.account in ("primary", "alt") else "primary"
        client = get_client(account)

        task["status"] = "authenticating"
        if not client.authenticate():
            task["status"] = "failed"
            task["error"] = f"WQ BRAIN 认证失败 (account={account})，请检查凭证配置"
            return

        task["status"] = "simulating"

        def on_progress(pct: int, message: str):
            task["progress"] = pct
            task["progress_message"] = message

        result = run_single_simulation(
            client, expression=req.expression,
            region=req.region, universe=req.universe,
            delay=req.delay, decay=req.decay,
            neutralization=req.neutralization, truncation=req.truncation,
            auto_submit=req.auto_submit and account == "primary",
            user_id=user_id, tag=req.tag,
            progress_callback=on_progress,
        )
        client.close()

        if not result.get("ok"):
            task["status"] = "failed"
            task["error"] = result.get("error", "WQ BRAIN simulation failed")
            return

        task["status"] = "completed"
        task["expression"] = req.expression
        task["result"] = result
        logger.info(f"[{task_id}] WQ BRAIN completed: alpha_id={result.get('alpha_id')} rating={result.get('interpretation', {}).get('rating')} submitted={result.get('submitted')}")

    except Exception as e:
        logger.error(f"[{task_id}] WQ BRAIN task error: {e}")
        task["status"] = "failed"
        task["error"] = f"WQ BRAIN 提交异常: {e}"
    finally:
        if "completed_at" not in task:
            task["completed_at"] = time.time()
        try:
            persist_task_to_db(task_id, user_id, task)
        except Exception as e:
            logger.error(f"[{task_id}] DB persist error: {e}")


@router.get("/status", summary="WQ BRAIN 配置状态")
async def wq_brain_status():
    accounts = configured_accounts()
    return {
        "configured": len(accounts) > 0,
        "accounts": accounts,
        "thresholds": SUBMIT_THRESHOLDS,
    }


@router.get("/user-info")
async def wq_brain_user_info(account: str = "primary"):
    if not is_configured(account):
        raise HTTPException(status_code=503, detail=f"WQ BRAIN 未配置 (account={account})")
    client = get_client(account)
    if not client.authenticate():
        raise HTTPException(status_code=502, detail="WQ BRAIN 认证失败")
    info = client.get_user_info()
    client.close()
    return info


@router.get("/platform-alphas")
async def list_platform_alphas(
    account: str = "primary",
    limit: int = 100,
    offset: int = 0,
    user: User = Depends(get_current_user),
):
    """List all alphas from WQ BRAIN platform (including simulated but not submitted)."""
    if not is_configured(account):
        raise HTTPException(status_code=503, detail=f"WQ BRAIN 未配置 (account={account})")
    client = get_client(account)
    if not client.authenticate():
        raise HTTPException(status_code=502, detail="WQ BRAIN 认证失败")
    result = run_list_alphas(client, limit=limit, offset=offset)
    client.close()
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error", "unknown"))
    return {"total": result["total"], "alphas": result["alphas"]}


@router.post("/submit", status_code=202, summary="提交因子到 WQ BRAIN 模拟")
async def wq_brain_submit(
    req: WQBrainSubmitRequest,
    request: Request,
    user: User = Depends(get_current_user),
):
    """提交因子表达式到 WorldQuant BRAIN 平台进行模拟。异步执行，返回 task_id。模拟通常需要 2-5 分钟，用 GET /api/v1/tasks/{task_id} 轮询结果。结果包含 Sharpe、Fitness、Turnover 等 IS 指标。"""
    if not is_configured(req.account):
        raise HTTPException(status_code=503, detail=f"WQ BRAIN 未配置 (account={req.account}) — 请设置对应的环境变量")

    client_ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")

    if active_task_count() >= MAX_ACTIVE_TASKS:
        raise HTTPException(status_code=503, detail="当前任务已满，请稍后再试")

    task_id = uuid.uuid4().hex[:12]
    user_id = str(user.id)

    with tasks_lock:
        tasks[task_id] = {
            "task_id": task_id,
            "user_id": user_id,
            "session_id": req.session_id,
            "status": "pending",
            "task_type": "wq_brain_submit",
            "cancelled": False,
            "params": req.model_dump(exclude={"session_id"}),
            "created_at": time.time(),
        }

    thread = threading.Thread(
        target=_run_wq_brain_task, args=(task_id, req, user_id), daemon=True,
    )
    thread.start()

    return {"task_id": task_id, "status": "pending"}


@router.get("/submitted-alphas", summary="查询已提交因子列表")
async def list_submitted_alphas(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    limit: int = 50,
    offset: int = 0,
):
    from sqlalchemy import func
    from sqlalchemy import select as sa_select

    from ..models import SubmittedAlpha

    count_q = await session.execute(
        sa_select(func.count()).where(SubmittedAlpha.user_id == user.id)
    )
    total = count_q.scalar() or 0

    q = await session.execute(
        sa_select(SubmittedAlpha)
        .where(SubmittedAlpha.user_id == user.id)
        .order_by(SubmittedAlpha.submitted_at.desc())
        .offset(offset)
        .limit(min(limit, 100))
    )
    alphas = q.scalars().all()

    return {
        "total": total,
        "alphas": [
            {
                "alpha_id": a.alpha_id,
                "expression": a.expression,
                "tag": a.tag,
                "region": a.region,
                "universe": a.universe,
                "delay": a.delay,
                "neutralization": a.neutralization,
                "sharpe": a.sharpe,
                "fitness": a.fitness,
                "returns": a.returns,
                "turnover": a.turnover,
                "status": a.status,
                "submitted_at": a.submitted_at.isoformat() if a.submitted_at else None,
            }
            for a in alphas
        ],
    }


@router.post("/{task_id}/submit-alpha")
async def submit_alpha_from_task(
    task_id: str,
    user: User = Depends(get_current_user),
):
    if not is_configured():
        raise HTTPException(status_code=503, detail="WQ BRAIN 未配置 — 无可用账号")

    task = tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    user_id = str(user.id)
    if task.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="无权操作此任务")

    result = task.get("result", {})
    alpha_id = result.get("alpha_id")
    if not alpha_id:
        raise HTTPException(status_code=400, detail="任务无关联的 alpha_id")

    account = task.get("params", {}).get("account", "primary")
    if account != "primary":
        raise HTTPException(status_code=403, detail="Alpha 提交仅允许 primary 账号，禁止从 alt 账号提交")
    client = get_client(account)
    if not client.authenticate():
        raise HTTPException(status_code=502, detail=f"WQ BRAIN 认证失败 (account={account})")

    submit_result = client.submit_alpha(alpha_id)
    client.close()
    logger.info(f"[{task_id}] submit_alpha({alpha_id}) result: {submit_result}")

    if submit_result.get("ok"):
        task["result"]["submitted"] = True
        try:
            from ..alpha_tracker import record_submitted_alpha_sync
            params = task.get("params", {})
            is_metrics = result.get("is_metrics", {})
            record_submitted_alpha_sync(
                user_id=user_id, alpha_id=alpha_id, expression=result.get("expression", ""),
                region=params.get("region", "USA"), universe=params.get("universe", "TOP3000"),
                delay=params.get("delay", 1), decay=params.get("decay", 0),
                neutralization=params.get("neutralization", "SUBINDUSTRY"),
                truncation=params.get("truncation", 0.08),
                sharpe=_safe_float(is_metrics.get("sharpe")),
                fitness=_safe_float(is_metrics.get("fitness")),
                returns=_safe_float(is_metrics.get("returns")),
                turnover=_safe_float(is_metrics.get("turnover")),
                tag=params.get("tag"),
            )
        except Exception as e:
            logger.warning(f"Alpha tracking failed for manual submit: {e}")

    return {
        "alpha_id": alpha_id,
        "submitted": submit_result.get("ok", False),
        "detail": submit_result.get("detail", ""),
    }


@router.get("/alpha-status/{alpha_id}")
async def check_alpha_platform_status(
    alpha_id: str,
    account: str = "primary",
    user: User = Depends(get_current_user),
):
    """Check actual platform-side alpha status (whether it's really submitted)."""
    if not is_configured(account):
        raise HTTPException(status_code=503, detail=f"WQ BRAIN 未配置 (account={account})")
    client = get_client(account)
    if not client.authenticate():
        raise HTTPException(status_code=502, detail=f"WQ BRAIN 认证失败 (account={account})")
    result = client.check_alpha_status(alpha_id)
    client.close()
    return result


@router.post("/submit-by-id/{alpha_id}")
async def submit_alpha_by_id(
    alpha_id: str,
    account: str = "primary",
    user: User = Depends(get_current_user),
):
    """Submit alpha directly by alpha_id. Polls until platform confirms or SC fails."""
    if account != "primary":
        raise HTTPException(status_code=403, detail="Alpha 提交仅允许 primary 账号")
    if not is_configured(account):
        raise HTTPException(status_code=503, detail="WQ BRAIN 未配置")
    client = get_client(account)
    if not client.authenticate():
        raise HTTPException(status_code=502, detail=f"WQ BRAIN 认证失败 (account={account})")
    result = client.submit_alpha(alpha_id)
    client.close()
    logger.info(f"submit-by-id {alpha_id}: {result}")
    return result


@router.delete("/alpha/{alpha_id}")
async def delete_alpha(
    alpha_id: str,
    account: str = "primary",
    user: User = Depends(get_current_user),
):
    """Delete/retire an alpha from the WQ BRAIN platform."""
    if not is_configured(account):
        raise HTTPException(status_code=503, detail="WQ BRAIN 未配置")
    client = get_client(account)
    if not client.authenticate():
        raise HTTPException(status_code=502, detail="WQ BRAIN 认证失败")
    result = client.delete_alpha(alpha_id)
    client.close()
    logger.info(f"delete-alpha {alpha_id}: {result}")
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("detail", "删除失败"))
    return result


@router.post("/alpha/{alpha_id}/unhide")
async def unhide_alpha(
    alpha_id: str,
    account: str = "primary",
    user: User = Depends(get_current_user),
):
    """Restore a hidden alpha on WQ BRAIN platform."""
    if not is_configured(account):
        raise HTTPException(status_code=503, detail="WQ BRAIN 未配置")
    client = get_client(account)
    if not client.authenticate():
        raise HTTPException(status_code=502, detail="WQ BRAIN 认证失败")
    result = client.unhide_alpha(alpha_id)
    client.close()
    logger.info(f"unhide-alpha {alpha_id}: {result}")
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("detail", "恢复失败"))
    return result
