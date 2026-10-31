"""WQ BRAIN batch operations — param sweep, batch submit by ID, batch status check, finalize."""

import logging
import os
import threading
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import get_current_user
from ..models import User
from ..task_store import (
    MAX_ACTIVE_TASKS,
    active_task_count,
    check_rate_limit,
    persist_task_to_db,
    tasks,
    tasks_lock,
)
from ..wq_brain_client import get_client, is_configured
from ..wq_brain_service import (
    run_batch_simulation,
    run_check_alphas,
    run_submit_by_ids,
    safe_float,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/wq-brain", tags=["wq_brain_batch"])

VALID_REGIONS = {"USA"}
VALID_UNIVERSES = {"TOP3000", "TOP1000", "TOP500", "TOP200"}
VALID_NEUTRALIZATIONS = {"MARKET", "SUBINDUSTRY", "INDUSTRY", "SECTOR", "NONE"}
MAX_COMBINATIONS = 36
MAX_BATCH_SUBMIT = 50
FINALIZE_POLL_INTERVAL = int(os.environ.get("WQ_FINALIZE_INTERVAL", "300"))
FINALIZE_MAX_WAIT = int(os.environ.get("WQ_FINALIZE_MAX_WAIT", "7200"))


class WQBrainBatchRequest(BaseModel):
    expression: str = Field(..., description="FASTEXPR factor expression")
    tag: str = Field(..., min_length=1, max_length=100, description="Submitter tag (e.g. 'agent-lowcorr-0506')")
    regions: list[str] = Field(default=["USA"], description="Regions to sweep")
    delays: list[int] = Field(default=[1], description="Delays to sweep")
    universes: list[str] = Field(default=["TOP3000"], description="Universes to sweep")
    neutralizations: list[str] = Field(default=["SUBINDUSTRY"], description="Neutralizations to sweep")
    decay: int = Field(0, ge=0, le=20, description="Alpha decay (shared)")
    truncation: float = Field(0.08, ge=0, le=0.5, description="Weight truncation (shared)")
    auto_submit: bool = Field(False, description="Auto-submit if all IS checks pass")
    account: str = Field("primary", description="WQ account: 'primary' or 'alt'")
    session_id: str | None = Field(None, description="Session ID")


class BatchSubmitByIdRequest(BaseModel):
    alpha_ids: list[str] = Field(..., min_length=1, max_length=MAX_BATCH_SUBMIT, description="Alpha IDs to submit")
    account: str = Field("primary", description="WQ account (must be 'primary' for submission)")


class BatchAlphaStatusRequest(BaseModel):
    alpha_ids: list[str] = Field(..., min_length=1, max_length=100, description="Alpha IDs to check")


class BatchFinalizeRequest(BaseModel):
    alpha_ids: list[str] = Field(..., min_length=1, max_length=100, description="Alpha IDs to finalize")
    account: str = Field("primary", description="WQ account")



_safe_float = safe_float


def _classify_alpha_check(data: dict) -> dict:
    """Classify a check_alpha_status() result into a final status."""
    if not data.get("ok"):
        return {
            "final_status": "ERROR",
            "status": None,
            "sc_result": None,
            "sc_value": None,
            "sc_limit": None,
            "fitness": None,
            "sharpe": None,
            "grade": None,
            "error": data.get("error", "unknown"),
        }

    status = (data.get("status") or "").upper()
    is_data = data.get("is", {})
    checks = is_data.get("checks", [])
    sc_check = next((c for c in checks if c.get("name") == "SELF_CORRELATION"), None)
    sc_result = sc_check.get("result") if sc_check else None

    if status == "ACTIVE":
        final = "ACTIVE"
    elif sc_result == "FAIL":
        final = "SC_FAIL"
    elif status == "UNSUBMITTED":
        final = "UNSUBMITTED"
    elif sc_result == "PENDING" or sc_result is None:
        final = "SC_PENDING"
    else:
        final = "OTHER_FAIL"

    return {
        "final_status": final,
        "status": status,
        "sc_result": sc_result,
        "sc_value": sc_check.get("value") if sc_check else None,
        "sc_limit": sc_check.get("limit") if sc_check else None,
        "fitness": _safe_float(is_data.get("fitness")),
        "sharpe": _safe_float(is_data.get("sharpe")),
        "grade": data.get("grade"),
    }


def _finalize_alpha_statuses(client, alpha_ids: list[str], user_id: str | None = None) -> dict:
    """Query platform for real SC results and update DB for resolved alphas."""
    results = {}
    summary = {"total": len(alpha_ids), "resolved": 0, "active": 0, "sc_fail": 0, "sc_pending": 0, "unsubmitted": 0, "error": 0}

    for alpha_id in alpha_ids:
        data = client.check_alpha_status(alpha_id)
        classified = _classify_alpha_check(data)
        results[alpha_id] = classified

        fs = classified["final_status"]
        if fs == "ACTIVE":
            summary["active"] += 1
            summary["resolved"] += 1
        elif fs == "SC_FAIL":
            summary["sc_fail"] += 1
            summary["resolved"] += 1
        elif fs == "UNSUBMITTED":
            summary["unsubmitted"] += 1
        elif fs == "SC_PENDING":
            summary["sc_pending"] += 1
        elif fs == "ERROR":
            summary["error"] += 1
        else:
            summary["resolved"] += 1

        if user_id and fs in ("ACTIVE", "SC_FAIL"):
            try:
                from ..alpha_tracker import update_submitted_alpha_status_sync
                update_submitted_alpha_status_sync(alpha_id, fs.lower())
            except Exception as e:
                logger.warning(f"Failed to update DB status for {alpha_id}: {e}")

    return {"summary": summary, "alphas": results}


def _run_batch_task(task_id: str, req: WQBrainBatchRequest, user_id: str):
    task = tasks.get(task_id)
    if not task:
        return

    task["completed_combinations"] = 0

    try:
        account = req.account if req.account in ("primary", "alt") else "primary"
        client = get_client(account)
        task["status"] = "authenticating"
        if not client.authenticate():
            task["status"] = "failed"
            task["error"] = f"WQ BRAIN 认证失败 (account={account})"
            return

        task["status"] = "running"

        def on_progress(current, total, key):
            task["progress_message"] = f"[{current}/{total}] {key}"
            task["completed_combinations"] = current

        result = run_batch_simulation(
            client, expression=req.expression,
            regions=req.regions, delays=req.delays,
            universes=req.universes, neutralizations=req.neutralizations,
            decay=req.decay, truncation=req.truncation,
            auto_submit=req.auto_submit and account == "primary",
            user_id=user_id, tag=req.tag,
            on_progress=on_progress,
            check_cancelled=lambda: task.get("cancelled", False),
        )
        client.close()

        if task.get("cancelled"):
            task["status"] = "cancelled"
        elif not result.get("ok"):
            task["status"] = "failed"
            task["error"] = result.get("error", "all simulations failed")
            task["result"] = result
        else:
            task["status"] = "completed"
            task["result"] = result

    except Exception as e:
        logger.error(f"[{task_id}] batch task error: {e}")
        task["status"] = "failed"
        task["error"] = f"批量提交异常: {e}"
    finally:
        if "completed_at" not in task:
            task["completed_at"] = time.time()
        try:
            persist_task_to_db(task_id, user_id, task)
        except Exception as e:
            logger.error(f"[{task_id}] DB persist error: {e}")


@router.post("/batch-submit", status_code=202, summary="批量提交因子到 WQ BRAIN")
async def wq_brain_batch_submit(
    req: WQBrainBatchRequest,
    request: Request,
    user: User = Depends(get_current_user),
):
    if not is_configured():
        raise HTTPException(status_code=503, detail="WQ BRAIN 未配置")

    for r in req.regions:
        if r not in VALID_REGIONS:
            raise HTTPException(status_code=400, detail=f"无效 region: {r}，可选: {sorted(VALID_REGIONS)}")
    for u in req.universes:
        if u not in VALID_UNIVERSES:
            raise HTTPException(status_code=400, detail=f"无效 universe: {u}，可选: {sorted(VALID_UNIVERSES)}")
    for n in req.neutralizations:
        if n not in VALID_NEUTRALIZATIONS:
            raise HTTPException(status_code=400, detail=f"无效 neutralization: {n}，可选: {sorted(VALID_NEUTRALIZATIONS)}")
    for d in req.delays:
        if d not in (0, 1):
            raise HTTPException(status_code=400, detail=f"无效 delay: {d}，可选: 0, 1")

    total = len(req.regions) * len(req.delays) * len(req.universes) * len(req.neutralizations)
    if total > MAX_COMBINATIONS:
        raise HTTPException(status_code=400, detail=f"组合数 {total} 超过上限 {MAX_COMBINATIONS}")

    client_ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="请求过于频繁")

    if active_task_count() >= MAX_ACTIVE_TASKS:
        raise HTTPException(status_code=503, detail="当前任务已满")

    task_id = uuid.uuid4().hex[:12]
    user_id = str(user.id)

    with tasks_lock:
        tasks[task_id] = {
            "task_id": task_id,
            "user_id": user_id,
            "session_id": req.session_id,
            "status": "pending",
            "task_type": "wq_brain_batch",
            "cancelled": False,
            "params": req.model_dump(exclude={"session_id"}),
            "expression": req.expression,
            "created_at": time.time(),
        }

    thread = threading.Thread(
        target=_run_batch_task, args=(task_id, req, user_id), daemon=True,
    )
    thread.start()

    return {"task_id": task_id, "status": "pending", "total_combinations": total}


# ---- Batch submit by alpha_id ----


def _run_batch_submit_by_id(task_id: str, alpha_ids: list[str], account: str, user_id: str):
    task = tasks.get(task_id)
    if not task:
        return

    task["completed"] = 0

    try:
        client = get_client(account)
        task["status"] = "authenticating"
        if not client.authenticate():
            task["status"] = "failed"
            task["error"] = f"WQ BRAIN 认证失败 (account={account})"
            return

        task["status"] = "running"

        def on_progress(current, total, aid):
            task["progress_message"] = f"[{current}/{total}] submitting {aid}"
            task["completed"] = current

        def on_each_done(aid, entry):
            task.setdefault("sub_results", {})[aid] = entry
            try:
                persist_task_to_db(task_id, user_id, task)
            except Exception as e:
                logger.warning(f"[{task_id}] incremental persist error: {e}")

        result = run_submit_by_ids(
            client, alpha_ids,
            on_progress=on_progress,
            check_cancelled=lambda: task.get("cancelled", False),
            on_each_done=on_each_done,
        )
        client.close()

        if task.get("cancelled"):
            task["status"] = "cancelled"
        else:
            task["status"] = "completed"
            task["result"] = result

        logger.info(
            f"[{task_id}] batch submit done: "
            f"{result.get('active', 0)} ACTIVE, {result.get('sc_fail', 0)} SC_FAIL, {result.get('timeout', 0)} TIMEOUT"
        )

    except Exception as e:
        logger.error(f"[{task_id}] batch submit error: {e}")
        task["status"] = "failed"
        task["error"] = f"批量提交异常: {e}"
    finally:
        if "completed_at" not in task:
            task["completed_at"] = time.time()
        try:
            persist_task_to_db(task_id, user_id, task)
        except Exception as e:
            logger.error(f"[{task_id}] DB persist error: {e}")


@router.post("/batch-submit-by-id", status_code=202, summary="批量提交已模拟因子")
async def wq_brain_batch_submit_by_id(
    req: BatchSubmitByIdRequest,
    request: Request,
    user: User = Depends(get_current_user),
):
    """Batch submit multiple already-simulated alphas by their alpha_id.

    Processes sequentially using one authenticated session.
    Returns a task_id for progress polling via GET /tasks/{task_id}.
    """
    if req.account != "primary":
        raise HTTPException(status_code=403, detail="Alpha 提交仅允许 primary 账号")
    if not is_configured(req.account):
        raise HTTPException(status_code=503, detail="WQ BRAIN 未配置")

    client_ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="请求过于频繁")

    task_id = uuid.uuid4().hex[:12]
    user_id = str(user.id)

    with tasks_lock:
        tasks[task_id] = {
            "task_id": task_id,
            "user_id": user_id,
            "status": "pending",
            "task_type": "wq_brain_batch_submit_by_id",
            "cancelled": False,
            "params": {"alpha_ids": req.alpha_ids, "account": req.account},
            "created_at": time.time(),
        }

    thread = threading.Thread(
        target=_run_batch_submit_by_id,
        args=(task_id, req.alpha_ids, req.account, user_id),
        daemon=True,
    )
    thread.start()

    return {"task_id": task_id, "status": "pending", "total": len(req.alpha_ids)}


# ---- Batch alpha status check (synchronous) ----


@router.post("/batch-alpha-status", summary="批量查询因子平台状态")
async def wq_brain_batch_alpha_status(
    req: BatchAlphaStatusRequest,
    user: User = Depends(get_current_user),
    account: str = "primary",
):
    """Check platform status of multiple alphas in one call.

    Returns status, fitness, sharpe, SC check result for each alpha_id.
    """
    if not is_configured(account):
        raise HTTPException(status_code=503, detail=f"WQ BRAIN 未配置 (account={account})")

    client = get_client(account)
    if not client.authenticate():
        raise HTTPException(status_code=502, detail="WQ BRAIN 认证失败")

    result = run_check_alphas(client, req.alpha_ids)
    client.close()
    return result


# ---- Batch finalize (query real SC results for previously submitted alphas) ----


@router.post("/batch-finalize", summary="批量查询 SC 检查最终结果")
async def wq_brain_batch_finalize(
    req: BatchFinalizeRequest,
    user: User = Depends(get_current_user),
):
    """Query final SC check results for previously submitted alphas.

    Use after batch-submit-by-id when SC checks timed out (SC PENDING).
    Updates SubmittedAlpha DB records for resolved alphas.
    """
    if not is_configured(req.account):
        raise HTTPException(status_code=503, detail=f"WQ BRAIN 未配置 (account={req.account})")

    client = get_client(req.account)
    if not client.authenticate():
        raise HTTPException(status_code=502, detail="WQ BRAIN 认证失败")

    result = _finalize_alpha_statuses(client, req.alpha_ids, user_id=str(user.id))
    client.close()

    return result
