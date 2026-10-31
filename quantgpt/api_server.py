"""REST API server for QuantGPT.

Endpoints:
    POST /api/v1/auto_backtest           — 提交回测任务（异步，立即返回 task_id）
    GET  /api/v1/tasks                   — 分页查询当前用户任务列表
    GET  /api/v1/tasks/{task_id}         — 查询任务状态和结果
    GET  /api/v1/tasks/{task_id}/stream  — SSE 实时推送任务状态
    POST /api/v1/tasks/{task_id}/iterate — 提交迭代优化任务
    POST /api/v1/tasks/{task_id}/select_candidate — 选择迭代候选因子
    GET  /api/v1/reports/{filename}      — 下载 HTML 报告
    POST /api/v1/feedback                — 提交问题反馈
    GET  /api/v1/health                  — 健康检查

启动: LLM_API_KEY=sk-xxx python -m quantgpt --transport http --port 8003
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, update

from . import task_store
from .db import close_db, init_db
from .models import Task as TaskModel
from .models import User

logger = logging.getLogger(__name__)


# ---- Lifespan ----

@asynccontextmanager
async def lifespan(app: FastAPI):
    task_store.main_loop = asyncio.get_running_loop()
    await init_db()
    logger.info("Database initialized")

    from .db import _get_session_factory as _sf
    async with _sf()() as session:
        result = await session.execute(
            update(TaskModel)
            .where(TaskModel.status.in_(["pending", "running", "generating_expression", "validating", "fetching_data", "backtesting"]))
            .values(status="failed", error="进程重启，任务中断")
        )
        if result.rowcount:
            await session.commit()
            logger.info(f"Cleaned up {result.rowcount} stale running tasks")

    from .auth import _DEV_USER_ID
    from .db import _get_session_factory
    async with _get_session_factory()() as session:
        result = await session.execute(select(User).where(User.id == _DEV_USER_ID))
        if not result.scalar_one_or_none():
            session.add(User(id=_DEV_USER_ID, email="dev@localhost", nickname="Local User"))
            await session.commit()
    logger.info("QuantGPT running in local mode (no auth)")

    from zoneinfo import ZoneInfo

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger

    from .scheduler_registry import record_job_run, register_job, register_scheduler
    CST = ZoneInfo("Asia/Shanghai")
    scheduler = AsyncIOScheduler()

    async def _market_data_refresh_job():
        import asyncio

        from .market_data import refresh_all_cached_stocks
        try:
            await asyncio.to_thread(refresh_all_cached_stocks)
            record_job_run("market_data_refresh", "success")
        except Exception as e:
            logger.error(f"Market data refresh failed: {e}")
            record_job_run("market_data_refresh", "failed", str(e))

    scheduler.add_job(_market_data_refresh_job, CronTrigger(hour=15, minute=10, day_of_week="mon-fri", timezone=CST), id="market_data_refresh")
    register_job("market_data_refresh", "行情数据增量更新", "收盘后从 akshare/baostock 增量更新缓存（禁用 rqdatac）", "周一至周五 15:10 CST")

    async def _weekly_report_job():
        from .db import _get_session_factory
        from .weekly_report import get_latest_report_content, send_weekly_report
        md = get_latest_report_content()
        if not md:
            logger.warning("Factor research job: no report file found")
            record_job_run("weekly_report", "skipped", "no report file found")
            return
        async with _get_session_factory()() as db:
            try:
                stats = await send_weekly_report(db, md)
                logger.info(f"Factor research job completed: {stats}")
                record_job_run("weekly_report", "success")
            except Exception as e:
                logger.error(f"Factor research job failed: {e}")
                record_job_run("weekly_report", "failed", str(e))

    scheduler.add_job(_weekly_report_job, CronTrigger(hour=9, minute=3, day_of_week="mon", timezone=CST), id="weekly_report")
    register_job("weekly_report", "因子研究周报", "每周一发送因子深度研究报告邮件给订阅用户", "每周一 09:03 CST")

    async def _daily_summary_job():
        from .daily_summary import generate_daily_summary
        from .db import _get_session_factory
        async with _get_session_factory()() as db:
            try:
                await generate_daily_summary(db, market="a_share")
                logger.info("Daily summary job completed")
                record_job_run("daily_summary", "success")
            except Exception as e:
                logger.error(f"Daily summary job failed: {e}")
                record_job_run("daily_summary", "failed", str(e))

    scheduler.add_job(_daily_summary_job, CronTrigger(hour=15, minute=30, day_of_week="mon-fri", timezone=CST), id="daily_summary")
    register_job("daily_summary", "每日大盘报告", "每个交易日收盘后生成因子信号驱动的市场解读报告", "周一至周五 15:30 CST")

    register_scheduler(scheduler)
    scheduler.start()
    logger.info("Scheduler started")

    from .mcp_server import mcp as _mcp_server
    _mcp_server.streamable_http_app()
    async with _mcp_server.session_manager.run():
        logger.info("MCP streamable-http session manager started")

        yield

    scheduler.shutdown(wait=False)
    from .task_executor import shutdown_executor
    shutdown_executor()
    await close_db()
    task_store.main_loop = None
    logger.info("Database connection closed")


# ---- App ----

_cors_origins = os.environ.get("QUANTGPT_CORS_ORIGINS", "*")
_cors_list = [o.strip() for o in _cors_origins.split(",") if o.strip()]

app = FastAPI(
    title="QuantGPT API",
    version="2.9.0",
    description="QuantGPT — Agent-Native 因子研究平台。AI Agent 通过本 API 自主完成因子设计、回测、评分、诊断、反过拟合检测和 WQ BRAIN 提交。",
    docs_url=None,
    redoc_url=None,
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_list,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)

# Register route modules
from .routes.admin import router as admin_router
from .routes.backtest_tasks import router as backtest_tasks_router
from .routes.cloud_upload import router as cloud_upload_router
from .routes.comparison import router as comparison_router
from .routes.composite import router as composite_router
from .routes.daily_summary import router as daily_summary_router
from .routes.factor_library import router as factor_library_router
from .routes.factor_values import router as factor_values_router
from .routes.feedback import router as feedback_router
from .routes.iteration_routes import router as iteration_router
from .routes.sessions import router as sessions_router
from .routes.wq_brain import router as wq_brain_router
from .routes.wq_brain_batch import router as wq_brain_batch_router

app.include_router(sessions_router)
app.include_router(admin_router)
app.include_router(factor_library_router)
app.include_router(composite_router)
app.include_router(comparison_router)
app.include_router(daily_summary_router)
app.include_router(backtest_tasks_router)
app.include_router(iteration_router)
app.include_router(feedback_router)
app.include_router(factor_values_router)
app.include_router(wq_brain_router)
app.include_router(wq_brain_batch_router)
app.include_router(cloud_upload_router)


# ---- robots.txt ----

_ROBOTS_TXT = """\
User-agent: *
Disallow: /api/
Disallow: /mcp/
Disallow: /mcp-sse/
Allow: /
"""


@app.get("/robots.txt")
async def robots_txt():
    return HTMLResponse(content=_ROBOTS_TXT, media_type="text/plain")


# ---- SPA static files (production: serve frontend/dist) ----

_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


def _mount_spa():
    if not _FRONTEND_DIST.is_dir():
        return

    assets_dir = _FRONTEND_DIST / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="frontend-assets")

    _index_html = _FRONTEND_DIST / "index.html"

    @app.get("/{full_path:path}")
    async def spa_fallback(request: Request, full_path: str):
        from fastapi import HTTPException
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        if full_path and not full_path.startswith("."):
            static_file = (_FRONTEND_DIST / full_path).resolve()
            if static_file.is_file() and static_file.is_relative_to(_FRONTEND_DIST.resolve()):
                from fastapi.responses import FileResponse
                return FileResponse(str(static_file))
        if _index_html.is_file():
            return HTMLResponse(_index_html.read_text(encoding="utf-8"))
        raise HTTPException(status_code=404, detail="Frontend not built")


# ---- Mount MCP at /mcp (both streamable-http and SSE) ----
from .mcp_server import mcp as _mcp_server

_mcp_app = _mcp_server.streamable_http_app()
_mcp_sse_app = _mcp_server.sse_app()
app.mount("/mcp", _mcp_app)
app.mount("/mcp-sse", _mcp_sse_app)

# ---- Serve chart images for email reports ----
_CHARTS_DIR = Path(__file__).resolve().parent.parent / "reports" / "charts"
_CHARTS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/charts", StaticFiles(directory=str(_CHARTS_DIR)), name="charts")


@app.middleware("http")
async def _mcp_path_rewrite(request: Request, call_next):
    if request.url.path == "/mcp":
        request.scope["path"] = "/mcp/"
    return await call_next(request)


_mount_spa()
