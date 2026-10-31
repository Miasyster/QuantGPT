"""Admin panel routes: login, overview, users, tasks, feedbacks."""

import logging
import uuid as uuid_mod
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_admin
from ..db import get_db
from ..models import ApiKey, Feedback, Task, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


class AdminLoginRequest(BaseModel):
    password: str


@router.post("/login")
async def admin_login(req: AdminLoginRequest):
    return {"token": "local-admin"}


@router.get("/overview", dependencies=[Depends(require_admin)])
async def admin_overview(db: AsyncSession = Depends(get_db)):
    """Aggregated stats: user count, task count, success rate, today active."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    user_count_q = await db.execute(select(func.count(User.id)))
    user_count = user_count_q.scalar() or 0

    task_count_q = await db.execute(select(func.count(Task.id)))
    task_count = task_count_q.scalar() or 0

    success_count_q = await db.execute(
        select(func.count(Task.id)).where(Task.status == "completed")
    )
    success_count = success_count_q.scalar() or 0
    success_rate = round(success_count / task_count * 100, 1) if task_count > 0 else 0

    today_active_q = await db.execute(
        select(func.count(func.distinct(Task.user_id))).where(
            Task.created_at >= today_start
        )
    )
    today_active = today_active_q.scalar() or 0

    feedback_count_q = await db.execute(select(func.count(Feedback.id)))
    feedback_count = feedback_count_q.scalar() or 0

    unresolved_q = await db.execute(
        select(func.count(Feedback.id)).where(Feedback.resolved == False)  # noqa: E712
    )
    unresolved_count = unresolved_q.scalar() or 0

    mcp_count_q = await db.execute(
        select(func.count(Task.id)).where(Task.task_type.like("mcp_%"))
    )
    mcp_task_count = mcp_count_q.scalar() or 0

    # Task status distribution (for pie chart)
    status_dist_q = await db.execute(
        select(Task.status, func.count(Task.id))
        .group_by(Task.status)
    )
    status_distribution = [
        {"name": row[0], "value": row[1]} for row in status_dist_q.all()
    ]

    # Daily task counts for last 7 days (for trend chart)
    seven_days_ago = (now - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
    daily_q = await db.execute(
        select(
            func.date_trunc("day", Task.created_at).label("day"),
            func.count(Task.id),
        )
        .where(Task.created_at >= seven_days_ago)
        .group_by("day")
        .order_by("day")
    )
    daily_map = {row[0].strftime("%m-%d"): row[1] for row in daily_q.all()}
    daily_tasks = []
    for i in range(7):
        d = seven_days_ago + timedelta(days=i)
        key = d.strftime("%m-%d")
        daily_tasks.append({"date": key, "count": daily_map.get(key, 0)})

    # Daily new user registrations for last 30 days (for user trend chart)
    thirty_days_ago = (now - timedelta(days=29)).replace(hour=0, minute=0, second=0, microsecond=0)
    daily_user_q = await db.execute(
        select(
            func.date_trunc("day", User.created_at).label("day"),
            func.count(User.id),
        )
        .where(User.created_at >= thirty_days_ago)
        .group_by("day")
        .order_by("day")
    )
    daily_user_map = {row[0].strftime("%m-%d"): row[1] for row in daily_user_q.all()}

    # Cumulative user count before the 30-day window
    base_user_q = await db.execute(
        select(func.count(User.id)).where(User.created_at < thirty_days_ago)
    )
    base_user_count = base_user_q.scalar() or 0

    user_trend = []
    cumulative = base_user_count
    for i in range(30):
        d = thirty_days_ago + timedelta(days=i)
        key = d.strftime("%m-%d")
        new_users = daily_user_map.get(key, 0)
        cumulative += new_users
        user_trend.append({"date": key, "new_users": new_users, "total_users": cumulative})

    return {
        "user_count": user_count,
        "task_count": task_count,
        "success_rate": success_rate,
        "today_active": today_active,
        "feedback_count": feedback_count,
        "unresolved_feedback_count": unresolved_count,
        "mcp_task_count": mcp_task_count,
        "status_distribution": status_distribution,
        "daily_tasks": daily_tasks,
        "user_trend": user_trend,
    }


@router.get("/users", dependencies=[Depends(require_admin)])
async def admin_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Paginated user list with task counts."""
    offset = (page - 1) * page_size

    # Total count
    total_q = await db.execute(select(func.count(User.id)))
    total = total_q.scalar() or 0

    # Users with task count subquery
    task_count_sub = (
        select(Task.user_id, func.count(Task.id).label("task_count"))
        .group_by(Task.user_id)
        .subquery()
    )

    query = (
        select(User, task_count_sub.c.task_count)
        .outerjoin(task_count_sub, User.id == task_count_sub.c.user_id)
        .order_by(desc(User.created_at))
        .offset(offset)
        .limit(page_size)
    )
    result = await db.execute(query)
    rows = result.all()

    users = []
    for user, task_count in rows:
        users.append({
            "id": str(user.id),
            "email": user.email,
            "nickname": user.nickname,
            "is_active": user.is_active,
            "task_count": task_count or 0,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        })

    return {"users": users, "total": total, "page": page, "page_size": page_size}


@router.get("/tasks", dependencies=[Depends(require_admin)])
async def admin_tasks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None, description="按状态过滤"),
    task_type: str | None = Query(None, description="按任务类型过滤"),
    user_id: str | None = Query(None, description="按用户 ID 过滤"),
    db: AsyncSession = Depends(get_db),
):
    """Paginated task list with filters."""
    offset = (page - 1) * page_size

    # Base count query
    count_query = select(func.count(Task.id))
    if status:
        count_query = count_query.where(Task.status == status)
    if task_type:
        count_query = count_query.where(Task.task_type == task_type)
    if user_id:
        count_query = count_query.where(Task.user_id == user_id)
    total_q = await db.execute(count_query)
    total = total_q.scalar() or 0

    # Main query with user email
    query = (
        select(Task, User.email)
        .outerjoin(User, Task.user_id == User.id)
    )
    if status:
        query = query.where(Task.status == status)
    if task_type:
        query = query.where(Task.task_type == task_type)
    if user_id:
        query = query.where(Task.user_id == user_id)
    query = query.order_by(desc(Task.created_at)).offset(offset).limit(page_size)

    result = await db.execute(query)
    rows = result.all()

    tasks = []
    for task, email in rows:
        tasks.append({
            "id": task.id,
            "user_email": email,
            "user_id": str(task.user_id),
            "task_type": task.task_type or "backtest",
            "status": task.status,
            "expression": task.expression,
            "params": task.params,
            "error": task.error,
            "created_at": task.created_at.isoformat() if task.created_at else None,
        })

    return {"tasks": tasks, "total": total, "page": page, "page_size": page_size}


@router.get("/feedbacks", dependencies=[Depends(require_admin)])
async def admin_feedbacks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Paginated feedback list."""
    offset = (page - 1) * page_size

    total_q = await db.execute(select(func.count(Feedback.id)))
    total = total_q.scalar() or 0

    query = (
        select(Feedback, User.email)
        .outerjoin(User, Feedback.user_id == User.id)
        .order_by(desc(Feedback.created_at))
        .offset(offset)
        .limit(page_size)
    )
    result = await db.execute(query)
    rows = result.all()

    feedbacks = []
    for fb, email in rows:
        feedbacks.append({
            "id": str(fb.id),
            "user_email": email,
            "description": fb.description,
            "screenshot_path": fb.screenshot_path,
            "task_id": fb.task_id,
            "resolved": fb.resolved,
            "resolved_at": fb.resolved_at.isoformat() if fb.resolved_at else None,
            "created_at": fb.created_at.isoformat() if fb.created_at else None,
        })

    return {"feedbacks": feedbacks, "total": total, "page": page, "page_size": page_size}


@router.patch("/feedbacks/{feedback_id}/resolve", dependencies=[Depends(require_admin)])
async def resolve_feedback(
    feedback_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Mark feedback as resolved."""
    result = await db.execute(select(Feedback).where(Feedback.id == uuid_mod.UUID(feedback_id)))
    fb = result.scalar_one_or_none()
    if not fb:
        raise HTTPException(status_code=404, detail="反馈不存在")

    fb.resolved = True
    fb.resolved_at = datetime.now(timezone.utc)
    await db.flush()

    # Send resolved notification email (fire-and-forget)
    if fb.user_id:
        user = await db.get(User, fb.user_id)
        if user and user.email:
            import asyncio

            from ..email_service import send_feedback_resolved_email

            async def _safe_send():
                try:
                    await send_feedback_resolved_email(user.email, str(fb.id), fb.description)
                except Exception as e:
                    logger.warning(f"Failed to send feedback resolved email to {user.email}: {e}")

            asyncio.create_task(_safe_send())

    return {"id": str(fb.id), "resolved": True, "resolved_at": fb.resolved_at.isoformat()}


# ---- Factor Deep Research Report ----


class SendTestReportRequest(BaseModel):
    email: str


@router.post("/weekly-report/send-test", dependencies=[Depends(require_admin)])
async def admin_send_test_report(req: SendTestReportRequest):
    """Send latest factor deep research report to a single test email."""
    from ..weekly_report import get_latest_report_content, send_weekly_report_to

    md = get_latest_report_content()
    if not md:
        raise HTTPException(status_code=404, detail="没有找到因子研究报告文件")

    ok = await send_weekly_report_to(req.email, md)
    if not ok:
        raise HTTPException(status_code=500, detail="发送失败，请检查 SMTP 配置和日志")
    return {"message": f"因子深度研究报告已发送到 {req.email}"}


@router.post("/weekly-report/send-all", dependencies=[Depends(require_admin)])
async def admin_send_all_reports(db: AsyncSession = Depends(get_db)):
    """Send latest factor deep research report to all subscribed users."""
    from ..weekly_report import get_latest_report_content, send_weekly_report

    md = get_latest_report_content()
    if not md:
        raise HTTPException(status_code=404, detail="没有找到因子研究报告文件")

    stats = await send_weekly_report(db, md)
    return stats


# ---- Scheduled Jobs ----

@router.get("/scheduled-jobs", dependencies=[Depends(require_admin)])
async def admin_scheduled_jobs():
    """List all scheduled jobs with status and next run time."""
    from ..scheduler_registry import get_jobs_info
    return {"jobs": get_jobs_info()}


@router.post("/scheduled-jobs/{job_id}/run", dependencies=[Depends(require_admin)])
async def admin_trigger_job(job_id: str):
    """Manually trigger a scheduled job."""
    from ..scheduler_registry import get_scheduler, record_job_run

    scheduler = get_scheduler()
    if not scheduler:
        raise HTTPException(status_code=503, detail="调度器未启动")

    job = scheduler.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"任务 {job_id} 不存在")

    try:
        record_job_run(job_id, "running")
        job.modify(next_run_time=datetime.now(timezone.utc))
        return {"message": f"任务 {job_id} 已触发"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/market-data/full-refresh", dependencies=[Depends(require_admin)])
async def admin_full_refresh():
    """Manually trigger rqdatac full refresh for all cached stocks."""
    import asyncio

    from ..market_data import enable_rqdatac, refresh_all_stocks_full

    def _run():
        with enable_rqdatac():
            refresh_all_stocks_full()

    try:
        await asyncio.to_thread(_run)
        return {"message": "rqdatac 全量刷新完成"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/market-data/rqdatac-incremental", dependencies=[Depends(require_admin)])
async def admin_rqdatac_incremental():
    """Manually trigger rqdatac incremental refresh."""
    import asyncio

    from ..market_data import enable_rqdatac, refresh_all_stocks_rqdatac_incremental

    def _run():
        with enable_rqdatac():
            refresh_all_stocks_rqdatac_incremental()

    try:
        await asyncio.to_thread(_run)
        return {"message": "rqdatac 增量刷新完成"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---- API Key Management ----


class CreateApiKeyRequest(BaseModel):
    user_email: str
    name: str | None = None


@router.post("/api-keys", dependencies=[Depends(require_admin)])
async def admin_create_api_key(req: CreateApiKeyRequest, db: AsyncSession = Depends(get_db)):
    return {"detail": "API keys not available — auth is disabled"}


@router.get("/api-keys", dependencies=[Depends(require_admin)])
async def admin_list_api_keys(db: AsyncSession = Depends(get_db)):
    """List all API Keys (prefix only, not full key)."""
    result = await db.execute(
        select(ApiKey, User.email)
        .join(User, ApiKey.user_id == User.id)
        .order_by(desc(ApiKey.created_at))
    )
    keys = []
    for ak, email in result.all():
        keys.append({
            "id": str(ak.id),
            "prefix": ak.prefix,
            "name": ak.name,
            "user_email": email,
            "is_active": ak.is_active,
            "last_used_at": ak.last_used_at.isoformat() if ak.last_used_at else None,
            "created_at": ak.created_at.isoformat(),
        })
    return {"api_keys": keys}


@router.delete("/api-keys/{key_id}", dependencies=[Depends(require_admin)])
async def admin_revoke_api_key(key_id: str, db: AsyncSession = Depends(get_db)):
    """Revoke (deactivate) an API Key."""
    result = await db.execute(select(ApiKey).where(ApiKey.id == uuid_mod.UUID(key_id)))
    ak = result.scalar_one_or_none()
    if not ak:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    ak.is_active = False
    await db.commit()
    return {"id": str(ak.id), "is_active": False}
