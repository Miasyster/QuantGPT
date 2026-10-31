"""FastMCP server for factor backtesting.

Provides tools for Agent-driven backtest workflow:
- list_operators: Show available factor expression operators
- list_universes: Show available stock universes
- validate_expression: Check expression syntax
- run_backtest: Execute full backtest pipeline
- score_factor: Compute composite factor quality score
- diagnose_factor: Diagnose factor issues and suggest mutations
- run_anti_overfit: Run anti-overfit detection
- run_rolling_validation: Walk-forward rolling validation
"""

import asyncio
import json
import logging
import sys
import traceback

import pandas as pd
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .expression_parser import __doc__ as _expr_module_doc
from .expression_parser import parse_expression
from .fundamental_data import ALL_FUNDAMENTAL_NAMES
from .market_data import BENCHMARK_CODES, UNIVERSES, MarketDataFetcher, fetch_benchmark_returns, get_universe
from .mcp_task_helper import complete_mcp_task, start_mcp_task
from .report import generate_report
from .task_executor import _run_backtest_in_process, get_executor
from .wq_brain_service import (
    run_batch_simulation,
    run_check_alphas,
    run_list_alphas,
    run_single_simulation,
    run_submit_by_ids,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s", stream=sys.stderr)
logger = logging.getLogger(__name__)

mcp = FastMCP(
    "quantgpt",
    instructions="QuantGPT — A 股因子回测服务。先用 list_operators 了解可用算子，再用 run_backtest 执行回测。可用 score_factor 评分、diagnose_factor 诊断、run_anti_overfit 检测过拟合、run_rolling_validation 滚动验证。",
    streamable_http_path="/",
    stateless_http=True,
    transport_security=TransportSecuritySettings(
        allowed_hosts=["localhost", "localhost:8003", "127.0.0.1", "127.0.0.1:8003"],
    ),
)


def _enrich_with_fundamentals(expression: str, market_df, stock_codes: list, start_date: str, end_date: str):
    """Conditionally fetch and merge fundamental data if the expression uses fundamental vars."""
    from .fundamental_data import detect_fundamental_vars, enrich_market_data
    fund_vars = detect_fundamental_vars(expression)
    return enrich_market_data(market_df, fund_vars, stock_codes, start_date, end_date)


def _fetch_data_for_market(universe: str, start_date: str, end_date: str):
    """Fetch market data and stock codes. Returns (market_df, stock_codes)."""
    stock_codes = get_universe(universe, date=start_date)
    fetcher = MarketDataFetcher()
    market_df = fetcher.fetch_stocks(stock_codes, start_date, end_date)
    return market_df, stock_codes


def _fetch_benchmark_for_market(benchmark: str, start_date: str, end_date: str):
    """Fetch benchmark returns."""
    return fetch_benchmark_returns(benchmark, start_date, end_date)


# Dummy DataFrame for expression validation (includes fundamental columns)
_VALIDATION_DUMMY = pd.DataFrame({
    "open": [1.0, 2.0, 3.0], "high": [1.1, 2.1, 3.1],
    "low": [0.9, 1.9, 2.9], "close": [1.0, 2.0, 3.0],
    "volume": [100, 200, 300], "amount": [100, 400, 900],
    "pct_change": [0, 100, 50],
    "trade_date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
    **{name: [1.0, 1.1, 1.2] for name in ALL_FUNDAMENTAL_NAMES},
})


@mcp.tool()
def list_operators() -> str:
    """返回因子表达式支持的全部操作符及用法说明。Agent 据此生成因子表达式。"""
    return _expr_module_doc or _OPERATORS_DOC


@mcp.tool()
def list_universes() -> str:
    """返回可用股票池列表及说明。"""
    a_share_info = {
        "small_scale": f"5 只蓝筹股（快速测试）: {UNIVERSES['small_scale']}",
        "hs300": "沪深300成分股（动态获取）",
        "csi500": "中证500成分股（动态获取）",
        "csi1000": "中证1000成分股（派生: 全A - HS300 - CSI500, 取前1000）",
        "csi2000": "中证2000成分股（派生: 全A - HS300 - CSI500 - CSI1000, 取前2000）",
    }
    a_share_benchmarks = {k: v["name"] for k, v in BENCHMARK_CODES.items()}
    return json.dumps({
        "universes": a_share_info,
        "benchmarks": a_share_benchmarks,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def validate_expression(expression: str, mode: str = "local") -> str:
    """验证因子表达式语法是否正确。返回 OK 或错误信息。

    Args:
        expression: 因子表达式
        mode: "local"（本地回测验证，默认）或 "wq"（WQ BRAIN 提交验证，放宽字段/算子限制）
    """

    depth = 0
    for i, ch in enumerate(expression):
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth < 0:
                return f"ERROR: 括号不平衡：位置 {i} 处多余的右括号 ')'"
    if depth > 0:
        return f"ERROR: 括号不平衡：缺少 {depth} 个右括号 ')'"

    try:
        func = parse_expression(expression, mode=mode)
        if mode == "wq":
            return "OK: expression is valid for WQ BRAIN submission"
        func(_VALIDATION_DUMMY)
        return "OK: expression is valid"
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
async def run_backtest(
    expression: str,
    universe: str = "hs300",
    start_date: str = "2023-01-01",
    end_date: str = "2025-12-31",
    n_groups: int = 5,
    holding_period: int = 5,
    benchmark: str = "hs300",
    neutralize_industry: bool = True,
    neutralize_cap: bool = True,
) -> str:
    """执行因子回测,生成 QuantStats HTML 报告。

    Args:
        expression: 因子表达式,如 "rank(close/ts_mean(close, 20))"
        universe: 股票池名称 (small_scale/hs300/csi500/csi1000/csi2000)
        start_date: 回测起始日期 YYYY-MM-DD
        end_date: 回测结束日期 YYYY-MM-DD
        n_groups: 分组数量
        holding_period: 持仓周期(交易日)
        benchmark: 基准 (hs300/zz500/sz50/csi1000)
        neutralize_industry: 行业中性化(默认开启)
        neutralize_cap: 市值中性化(默认开启)

    Returns:
        JSON string with report_path, metrics, group_returns, anti_overfit.
    """
    task_id = await start_mcp_task("backtest", expression, {
        "universe": universe, "start_date": start_date, "end_date": end_date,
        "n_groups": n_groups, "holding_period": holding_period, "benchmark": benchmark,
        "neutralize_industry": neutralize_industry, "neutralize_cap": neutralize_cap,
    })
    _error_msg = None
    _result = None
    try:
        logger.info(f"Getting universe: {universe}")
        market_df, stock_codes = await asyncio.to_thread(_fetch_data_for_market, universe, start_date, end_date)
        if market_df is None or len(market_df) == 0:
            return json.dumps({"error": "No market data available. Check date range and stock codes."})

        market_df = await asyncio.to_thread(_enrich_with_fundamentals, expression, market_df, stock_codes, start_date, end_date)

        logger.info(f"Running backtest: {expression}")
        executor = get_executor()
        future = executor.submit_cpu_work(
            _run_backtest_in_process, market_df, expression, n_groups, holding_period,
            neutralize_industry=neutralize_industry, neutralize_cap=neutralize_cap,
        )
        result = await asyncio.to_thread(future.result, 600)

        anti_overfit_result = None
        factor_df = result.get("_factor_df")
        if factor_df is not None and len(factor_df) > 100:
            try:
                from .anti_overfit import run_anti_overfit as _run_ao
                anti_overfit_result = await asyncio.to_thread(_run_ao, factor_df, holding_period)
            except Exception as e:
                logger.warning(f"Anti-overfit analysis failed: {e}")

        bm_returns = None
        try:
            bm_returns = await asyncio.to_thread(_fetch_benchmark_for_market, benchmark, start_date, end_date)
        except Exception as e:
            logger.warning(f"Benchmark fetch failed: {e}")

        report_result = await asyncio.to_thread(
            generate_report,
            result["ls_returns"],
            benchmark_returns=bm_returns,
            title=f"Factor: {expression}",
        )

        _result = {
            "report_path": report_result["report_path"],
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
                "cost_adjusted": result.get("cost_adjusted", False),
                "cost_rate": result.get("cost_rate", 0),
                "total_cost_drag": result.get("total_cost_drag", 0),
            },
            "wq_brain": result.get("wq_brain", {}),
            "anti_overfit": anti_overfit_result,
            "params": {
                "expression": expression,
                "universe": universe,
                "start_date": start_date,
                "end_date": end_date,
                "n_groups": n_groups,
                "holding_period": holding_period,
                "benchmark": benchmark,
                "neutralize_industry": neutralize_industry,
                "neutralize_cap": neutralize_cap,
                "stock_count": len(stock_codes),
            },
        }
        return json.dumps(_result, ensure_ascii=False, indent=2, default=str)

    except Exception as e:
        logger.error(f"Backtest failed: {traceback.format_exc()}")
        _error_msg = str(e)
        return json.dumps({"error": str(e)})
    finally:
        await complete_mcp_task(task_id, _result, _error_msg, expression)


@mcp.tool()
async def score_factor(
    expression: str,
    universe: str = "hs300",
    start_date: str = "2023-01-01",
    end_date: str = "2025-12-31",
    n_groups: int = 5,
    holding_period: int = 5,
    benchmark: str = "hs300",
    neutralize_industry: bool = True,
    neutralize_cap: bool = True,
) -> str:
    """执行因子回测并返回综合评分(0-100)和等级(A/B/C/D)。

    比 run_backtest 更轻量,不生成 HTML 报告,专注评分。

    Args:
        expression: 因子表达式
        universe: 股票池 (small_scale/hs300/csi500/csi1000/csi2000)
        start_date: 起始日期 YYYY-MM-DD
        end_date: 结束日期 YYYY-MM-DD
        n_groups: 分组数量
        holding_period: 持仓周期(交易日)
        benchmark: 基准 (hs300/zz500/sz50/csi1000)
        neutralize_industry: 行业中性化(默认开启)
        neutralize_cap: 市值中性化(默认开启)

    Returns:
        JSON with score, grade, component_scores, key metrics.
    """
    from .iteration import compute_factor_score

    task_id = await start_mcp_task("score", expression, {
        "universe": universe, "start_date": start_date, "end_date": end_date,
        "n_groups": n_groups, "holding_period": holding_period, "benchmark": benchmark,
    })
    _error_msg = None
    _result = None
    try:
        market_df, stock_codes = await asyncio.to_thread(_fetch_data_for_market, universe, start_date, end_date)
        if market_df is None or len(market_df) == 0:
            return json.dumps({"error": "No market data available."})

        market_df = await asyncio.to_thread(_enrich_with_fundamentals, expression, market_df, stock_codes, start_date, end_date)

        executor = get_executor()
        future = executor.submit_cpu_work(
            _run_backtest_in_process, market_df, expression, n_groups, holding_period,
            neutralize_industry=neutralize_industry, neutralize_cap=neutralize_cap,
        )
        result = await asyncio.to_thread(future.result, 600)

        bm_returns = None
        try:
            bm_returns = await asyncio.to_thread(_fetch_benchmark_for_market, benchmark, start_date, end_date)
        except Exception:
            pass

        report_result = await asyncio.to_thread(
            generate_report,
            result["ls_returns"],
            benchmark_returns=bm_returns,
            title="Factor Score",
        )

        scoring = compute_factor_score(
            backtest_summary={
                "long_short_sharpe": result["long_short_sharpe"],
                "monotonicity_score": result["monotonicity_score"],
                "spread": result["spread"],
                "ic_mean": result.get("ic_mean", 0),
                "rank_ic_mean": result.get("rank_ic_mean", 0),
                "ic_ir": result.get("ic_ir", 0),
                "ic_win_rate": result.get("ic_win_rate", 0),
            },
            report_metrics=report_result["metrics"],
        )

        _result = {
            "score": scoring["score"],
            "grade": scoring["grade"],
            "component_scores": scoring["component_scores"],
            "key_metrics": {
                "ic_mean": result.get("ic_mean", 0),
                "ic_ir": result.get("ic_ir", 0),
                "monotonicity": result["monotonicity_score"],
                "top_group_sharpe": result.get("top_group_sharpe", 0),
                "turnover": result.get("turnover", 0),
                "sharpe": report_result["metrics"].get("sharpe", 0),
                "max_drawdown": report_result["metrics"].get("max_drawdown", 0),
            },
            "interpretation": {"rating": scoring["grade"]},
        }
        return json.dumps(_result, ensure_ascii=False, indent=2, default=str)

    except Exception as e:
        logger.error(f"Score failed: {traceback.format_exc()}")
        _error_msg = str(e)
        return json.dumps({"error": str(e)})
    finally:
        await complete_mcp_task(task_id, _result, _error_msg, expression)


@mcp.tool()
def diagnose_factor(
    expression: str,
    ic_mean: float = 0.0,
    ic_ir: float = 0.0,
    monotonicity_score: float = 0.0,
    score: float = 50.0,
) -> str:
    """诊断因子问题并推荐突变策略。

    根据因子的 IC/IR/单调性/评分,判断失败模式(IC为零、IC为负、嵌套过深等),
    返回推荐的改进策略和定向 LLM 提示词。

    Args:
        expression: 当前因子表达式
        ic_mean: IC 均值
        ic_ir: IC 信息比率
        monotonicity_score: 分组单调性 (0-1)
        score: 综合评分 (0-100)

    Returns:
        JSON with diagnosis strategy, reason, and suggested mutation prompt.
    """
    from .mutation_engine import MutationEngine

    try:
        engine = MutationEngine(
            expression=expression,
            metrics={
                "backtest_summary": {
                    "ic_mean": ic_mean,
                    "ic_ir": ic_ir,
                    "monotonicity_score": monotonicity_score,
                },
                "report_metrics": {},
            },
            score=score,
        )
        diagnosis = engine.diagnose_failure()
        sys_prompt, user_prompt = engine.build_mutation_prompt()

        output = {
            "strategy": diagnosis.strategy.value,
            "reason": diagnosis.reason,
            "details": diagnosis.details,
            "mutation_prompt": {
                "system": sys_prompt[:500] + "..." if len(sys_prompt) > 500 else sys_prompt,
                "user": user_prompt,
            },
        }
        return json.dumps(output, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error(f"Diagnose failed: {traceback.format_exc()}")
        return json.dumps({"error": str(e)})


@mcp.tool()
async def run_anti_overfit(
    expression: str,
    universe: str = "hs300",
    start_date: str = "2023-01-01",
    end_date: str = "2025-12-31",
    holding_period: int = 5,
    neutralize_industry: bool = True,
    neutralize_cap: bool = True,
) -> str:
    """对因子执行反过拟合检测(4项测试)。

    测试项: IC稳定性、子样本压力、安慰剂检验、半衰期估计。
    返回总分(0-100)和各测试通过情况。

    Args:
        expression: 因子表达式
        universe: 股票池 (small_scale/hs300/csi500/csi1000/csi2000)
        start_date: 起始日期 YYYY-MM-DD
        end_date: 结束日期 YYYY-MM-DD
        holding_period: 持仓周期(交易日)
        neutralize_industry: 行业中性化(默认开启)
        neutralize_cap: 市值中性化(默认开启)

    Returns:
        JSON with score, recommendation, and per-test details.
    """
    from .anti_overfit import run_anti_overfit as _run_ao

    task_id = await start_mcp_task("anti_overfit", expression, {
        "universe": universe, "start_date": start_date, "end_date": end_date,
        "holding_period": holding_period,
    })
    _error_msg = None
    _result = None
    try:
        market_df, stock_codes = await asyncio.to_thread(_fetch_data_for_market, universe, start_date, end_date)
        if market_df is None or len(market_df) == 0:
            return json.dumps({"error": "No market data available."})

        market_df = await asyncio.to_thread(_enrich_with_fundamentals, expression, market_df, stock_codes, start_date, end_date)

        executor = get_executor()
        future = executor.submit_cpu_work(
            _run_backtest_in_process, market_df, expression, 5, holding_period,
            cost_rate=0,
            neutralize_industry=neutralize_industry, neutralize_cap=neutralize_cap,
        )
        result = await asyncio.to_thread(future.result, 600)
        factor_df = result.get("_factor_df")
        if factor_df is None or len(factor_df) < 100:
            return json.dumps({"error": "Insufficient factor data for anti-overfit analysis."})

        _result = await asyncio.to_thread(_run_ao, factor_df, holding_period)
        return json.dumps(_result, ensure_ascii=False, indent=2, default=str)

    except Exception as e:
        logger.error(f"Anti-overfit failed: {traceback.format_exc()}")
        _error_msg = str(e)
        return json.dumps({"error": str(e)})
    finally:
        await complete_mcp_task(task_id, _result, _error_msg, expression)


@mcp.tool()
async def run_rolling_validation(
    expression: str,
    universe: str = "hs300",
    start_date: str = "2020-01-01",
    end_date: str = "2025-12-31",
    holding_period: int = 5,
    neutralize_industry: bool = True,
    neutralize_cap: bool = True,
) -> str:
    """对因子执行滚动验证(Walk-Forward)。

    将数据切分为多个 训练/验证/测试 窗口(默认 3年/1年/1年,步长3个月),
    计算每个窗口的 IC/IR,评估因子在样本外的衰减情况。

    Args:
        expression: 因子表达式
        universe: 股票池 (small_scale/hs300/csi500/csi1000/csi2000)
        start_date: 起始日期(建议≥5年数据)
        end_date: 结束日期
        holding_period: 持仓周期(交易日)
        neutralize_industry: 行业中性化(默认开启)
        neutralize_cap: 市值中性化(默认开启)

    Returns:
        JSON with composite score, per-window results, decay analysis.
    """
    from .rolling_validator import run_rolling_validation as _run_rv

    task_id = await start_mcp_task("rolling_validation", expression, {
        "universe": universe, "start_date": start_date, "end_date": end_date,
        "holding_period": holding_period,
    })
    _error_msg = None
    _result = None
    try:
        market_df, stock_codes = await asyncio.to_thread(_fetch_data_for_market, universe, start_date, end_date)
        if market_df is None or len(market_df) == 0:
            return json.dumps({"error": "No market data available."})

        market_df = await asyncio.to_thread(_enrich_with_fundamentals, expression, market_df, stock_codes, start_date, end_date)

        executor = get_executor()
        future = executor.submit_cpu_work(
            _run_backtest_in_process, market_df, expression, 5, holding_period,
            cost_rate=0,
            neutralize_industry=neutralize_industry, neutralize_cap=neutralize_cap,
        )
        result = await asyncio.to_thread(future.result, 600)
        factor_df = result.get("_factor_df")
        if factor_df is None or len(factor_df) < 100:
            return json.dumps({"error": "Insufficient factor data for rolling validation."})

        _result = await asyncio.to_thread(_run_rv, factor_df, holding_period)
        return json.dumps(_result, ensure_ascii=False, indent=2, default=str)

    except Exception as e:
        logger.error(f"Rolling validation failed: {traceback.format_exc()}")
        _error_msg = str(e)
        return json.dumps({"error": str(e)})
    finally:
        await complete_mcp_task(task_id, _result, _error_msg, expression)


@mcp.tool()
async def wq_brain_submit(
    expression: str,
    tag: str,
    region: str = "USA",
    universe: str = "TOP3000",
    delay: int = 1,
    decay: int = 0,
    neutralization: str = "SUBINDUSTRY",
    truncation: float = 0.08,
    auto_submit: bool = False,
) -> str:
    """提交因子表达式到 WorldQuant BRAIN 平台进行真实模拟。

    与 run_backtest（本地 A 股回测）不同，此工具调用 WQ BRAIN 真实 API，
    在美股 TOP3000 等市场上评估因子。返回样本内/样本外指标和提交资格。

    需要在 .env 中配置 WQ_BRAIN_EMAIL 和 WQ_BRAIN_PASSWORD。

    Args:
        expression: FASTEXPR 表达式 (如 "rank(close/open)")
        tag: 提交者标识 (如 "agent-lowcorr-0506")，用于追踪哪个 agent 提交
        region: 市场区域 (当前仅 USA 可用)
        universe: WQ Universe (TOP3000, TOP500 等)
        delay: 信号延迟 (0 或 1)
        decay: Alpha 衰减 (0-20)
        neutralization: 中性化 (SUBINDUSTRY, INDUSTRY, SECTOR, MARKET, NONE)
        truncation: 权重截断 (0-0.5)
        auto_submit: 如果检查全部通过，自动提交到 WQ 审核

    Returns:
        JSON with IS/OOS metrics, alpha_id, checks, submittable status.
    """
    from .wq_brain_client import get_client
    from .wq_brain_client import is_configured as _wq_configured

    task_id = await start_mcp_task("wq_brain_submit", expression, {
        "expression": expression, "tag": tag, "region": region, "universe": universe,
        "delay": delay, "decay": decay, "neutralization": neutralization,
        "truncation": truncation, "auto_submit": auto_submit,
    })
    _error_msg = None
    _result = None
    try:
        if not _wq_configured():
            return json.dumps({"error": "WQ BRAIN 未配置 — 请设置 WQ_BRAIN_EMAIL 和 WQ_BRAIN_PASSWORD"})

        client = get_client("primary")
        authenticated = await asyncio.to_thread(client.authenticate)
        if not authenticated:
            return json.dumps({"error": "WQ BRAIN 认证失败"})

        _result = await asyncio.to_thread(
            run_single_simulation, client,
            expression, region=region, universe=universe,
            delay=delay, decay=decay, neutralization=neutralization,
            truncation=truncation, auto_submit=auto_submit, tag=tag,
        )
        await asyncio.to_thread(client.close)

        if not _result.get("ok"):
            _error_msg = _result.get("error", "Simulation failed")
            return json.dumps({"error": _error_msg})

        return json.dumps(_result, ensure_ascii=False, indent=2, default=str)

    except Exception as e:
        logger.error(f"WQ BRAIN submit failed: {traceback.format_exc()}")
        _error_msg = str(e)
        return json.dumps({"error": str(e)})
    finally:
        await complete_mcp_task(task_id, _result, _error_msg, expression)


@mcp.tool()
async def wq_brain_batch_submit(
    expression: str,
    tag: str,
    regions: list[str] | None = None,
    delays: list[int] | None = None,
    universes: list[str] | None = None,
    neutralizations: list[str] | None = None,
    decay: int = 0,
    truncation: float = 0.08,
    auto_submit: bool = False,
) -> str:
    """批量扫描因子表达式在多个参数组合下的 WQ BRAIN 表现。

    在 region × delay × universe × neutralization 的网格上逐一模拟，
    返回每个组合的 IS 指标和最优组合。适合找出同一表达式的最佳参数。

    Args:
        expression: FASTEXPR 表达式
        tag: 提交者标识 (如 "agent-lowcorr-0506")，用于追踪哪个 agent 提交
        regions: 市场区域列表 (默认 ["USA"])
        delays: 信号延迟列表 (默认 [1])
        universes: Universe 列表 (默认 ["TOP3000"])
        neutralizations: 中性化列表 (默认 ["SUBINDUSTRY"])
        decay: Alpha 衰减 (0-20, 共用)
        truncation: 权重截断 (0-0.5, 共用)
        auto_submit: 全部检查通过时自动提交

    Returns:
        JSON with per-combination results, best_fitness, submittable_count.
    """
    from .wq_brain_client import get_client
    from .wq_brain_client import is_configured as _wq_configured

    regions = regions or ["USA"]
    delays = delays or [1]
    universes = universes or ["TOP3000"]
    neutralizations = neutralizations or ["SUBINDUSTRY"]

    task_id = await start_mcp_task("wq_brain_batch", expression, {
        "expression": expression, "tag": tag,
        "regions": regions, "delays": delays, "universes": universes,
        "neutralizations": neutralizations, "decay": decay, "truncation": truncation,
        "auto_submit": auto_submit,
    })
    _error_msg = None
    _result = None
    try:
        if not _wq_configured():
            return json.dumps({"error": "WQ BRAIN 未配置 — 请设置 WQ_BRAIN_EMAIL 和 WQ_BRAIN_PASSWORD"})

        total = len(regions) * len(delays) * len(universes) * len(neutralizations)
        if total > 36:
            return json.dumps({"error": f"组合数 {total} 超过上限 36"})

        client = get_client("primary")
        authenticated = await asyncio.to_thread(client.authenticate)
        if not authenticated:
            return json.dumps({"error": "WQ BRAIN 认证失败"})

        _result = await asyncio.to_thread(
            run_batch_simulation, client, expression,
            regions=regions, delays=delays, universes=universes,
            neutralizations=neutralizations, decay=decay, truncation=truncation,
            auto_submit=auto_submit, tag=tag,
        )
        await asyncio.to_thread(client.close)

        if not _result.get("ok"):
            _error_msg = _result.get("error")
        return json.dumps(_result, ensure_ascii=False, indent=2, default=str)

    except Exception as e:
        logger.error(f"WQ BRAIN batch failed: {traceback.format_exc()}")
        _error_msg = str(e)
        return json.dumps({"error": str(e)})
    finally:
        await complete_mcp_task(task_id, _result, _error_msg, expression)


@mcp.tool()
async def wq_brain_submit_by_ids(
    alpha_ids: list[str],
    account: str = "primary",
) -> str:
    """批量提交已模拟的 alpha（通过 alpha_id 直接提交，无需重新模拟）。

    用于提交之前模拟过但未正式提交的 A 级 alpha。逐个处理，
    每个 alpha 等待 SC 检查结果（最长 120s）。

    Args:
        alpha_ids: 要提交的 alpha_id 列表（最多 50 个）
        account: WQ 账号（提交只能用 'primary'）

    Returns:
        JSON with per-alpha result (ACTIVE/SC_FAIL/TIMEOUT) and summary.
    """
    from .wq_brain_client import get_client
    from .wq_brain_client import is_configured as _wq_configured

    if account != "primary":
        return json.dumps({"error": "Alpha 提交仅允许 primary 账号"})
    if not _wq_configured(account):
        return json.dumps({"error": "WQ BRAIN 未配置"})
    if len(alpha_ids) > 50:
        return json.dumps({"error": f"alpha_ids 数量 {len(alpha_ids)} 超过上限 50"})

    task_id = await start_mcp_task(
        "wq_brain_submit_by_ids",
        None,
        {"alpha_ids": alpha_ids, "account": account},
    )
    _result = None
    _error_msg = None

    try:
        client = get_client(account)
        authenticated = await asyncio.to_thread(client.authenticate)
        if not authenticated:
            _error_msg = "WQ BRAIN 认证失败"
            return json.dumps({"error": _error_msg})

        _result = await asyncio.to_thread(run_submit_by_ids, client, alpha_ids)
        await asyncio.to_thread(client.close)

        return json.dumps(_result, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        _error_msg = str(e)
        return json.dumps({"error": _error_msg})
    finally:
        await complete_mcp_task(task_id, _result, _error_msg)


@mcp.tool()
async def wq_brain_list_alphas(
    account: str = "primary",
    limit: int = 100,
    offset: int = 0,
    min_fitness: float | None = None,
    status_filter: str | None = None,
) -> str:
    """列出 WQ BRAIN 平台上的所有 alpha（包括已模拟未提交的）。

    可按 fitness 下限和状态过滤。返回 alpha_id、表达式、指标。

    Args:
        account: WQ 账号 ('primary' 或 'alt')
        limit: 返回数量上限（最大 100）
        offset: 分页偏移
        min_fitness: 最低 fitness 过滤（如 1.0 只看 A 级）
        status_filter: 状态过滤（如 'UNSUBMITTED' 或 'ACTIVE'）

    Returns:
        JSON with alpha list, each containing alpha_id, expression, metrics.
    """
    from .wq_brain_client import get_client
    from .wq_brain_client import is_configured as _wq_configured

    if not _wq_configured(account):
        return json.dumps({"error": f"WQ BRAIN 未配置 (account={account})"})

    client = get_client(account)
    authenticated = await asyncio.to_thread(client.authenticate)
    if not authenticated:
        return json.dumps({"error": "WQ BRAIN 认证失败"})

    result = await asyncio.to_thread(
        run_list_alphas, client,
        limit=limit, offset=offset,
        min_fitness=min_fitness, status_filter=status_filter,
    )
    await asyncio.to_thread(client.close)

    if not result.get("ok"):
        return json.dumps({"error": result.get("error", "unknown")})

    return json.dumps(result, ensure_ascii=False, indent=2, default=str)


@mcp.tool()
async def wq_brain_check_alphas(
    alpha_ids: list[str],
    account: str = "primary",
) -> str:
    """批量查询 alpha 在 WQ BRAIN 平台上的状态。

    返回每个 alpha 的状态（ACTIVE/UNSUBMITTED）、SC 检查结果、指标。

    Args:
        alpha_ids: 要查询的 alpha_id 列表（最多 50 个）
        account: WQ 账号 ('primary' 或 'alt')

    Returns:
        JSON with summary and per-alpha status.
    """
    from .wq_brain_client import get_client
    from .wq_brain_client import is_configured as _wq_configured

    if not _wq_configured(account):
        return json.dumps({"error": f"WQ BRAIN 未配置 (account={account})"})
    if len(alpha_ids) > 50:
        return json.dumps({"error": f"alpha_ids 数量 {len(alpha_ids)} 超过上限 50"})

    client = get_client(account)
    authenticated = await asyncio.to_thread(client.authenticate)
    if not authenticated:
        return json.dumps({"error": "WQ BRAIN 认证失败"})

    result = await asyncio.to_thread(run_check_alphas, client, alpha_ids)
    await asyncio.to_thread(client.close)

    return json.dumps(result, ensure_ascii=False, indent=2, default=str)


@mcp.tool()
async def wq_brain_finalize_submissions(
    alpha_ids: list[str],
    account: str = "primary",
) -> str:
    """查询已提交 alpha 的最终 SC 检查结果。

    提交 alpha 后 SC 检查可能需要数小时。初次提交 SC 超时的 alpha 用此工具查询最终结果。
    会自动更新 DB 中已解决 alpha 的状态（ACTIVE / SC_FAIL）。

    Args:
        alpha_ids: 要查询最终状态的 alpha_id 列表（最多 100 个）
        account: WQ 账号 ('primary' 或 'alt')

    Returns:
        JSON: per-alpha final_status (ACTIVE/SC_FAIL/SC_PENDING/ERROR) + summary
    """
    task_id = await start_mcp_task("wq_brain_finalize", None, {
        "alpha_ids": alpha_ids[:10], "account": account, "total": len(alpha_ids),
    })
    _error_msg = None
    _result = None
    try:
        from .routes.wq_brain_batch import _finalize_alpha_statuses
        from .wq_brain_client import get_client
        from .wq_brain_client import is_configured as _wq_configured

        if not _wq_configured(account):
            return json.dumps({"error": f"WQ BRAIN 未配置 (account={account})"})
        if len(alpha_ids) > 100:
            return json.dumps({"error": f"alpha_ids 数量 {len(alpha_ids)} 超过上限 100"})

        client = get_client(account)
        authenticated = await asyncio.to_thread(client.authenticate)
        if not authenticated:
            return json.dumps({"error": "WQ BRAIN 认证失败"})

        _result = await asyncio.to_thread(
            _finalize_alpha_statuses, client, alpha_ids, None,
        )

        await asyncio.to_thread(client.close)

        return json.dumps(_result, ensure_ascii=False, indent=2, default=str)

    except Exception as e:
        _error_msg = str(e)
        logger.error(f"MCP wq_brain_finalize error: {e}")
        return json.dumps({"error": f"Finalize failed: {e}"})
    finally:
        await complete_mcp_task(task_id, _result, _error_msg)


@mcp.tool()
async def compute_factor_values(
    expression: str,
    universe: str = "csi500",
    start_date: str = "",
    end_date: str = "",
) -> str:
    """计算因子截面值 — 返回每个交易日所有股票的因子得分。

    用于下游组合构建、因子库上传或外部分析。
    输出格式: {trading_days, data: [{date, values: {symbol: score}, count}]}

    Args:
        expression: 因子表达式（如 rank(ts_mean(close/open, 10))）
        universe: 股票池（hs300 / csi500 / csi1000 / csi2000）
        start_date: 起始日期 YYYY-MM-DD（默认 end_date 前 365 天）
        end_date: 截止日期 YYYY-MM-DD（默认今天）
    """
    import numpy as np

    from .backtest import api_context
    from .expression_parser import parse_expression
    from .market_data import MarketDataFetcher, get_universe

    try:
        factor_fn = parse_expression(expression)
    except Exception as e:
        return json.dumps({"error": f"Invalid expression: {e}"}, ensure_ascii=False)

    def _compute():
        from datetime import date, timedelta

        with api_context():
            fetcher = MarketDataFetcher()
            stocks = get_universe(universe)
            if not stocks:
                return {"error": f"Empty universe: {universe}"}

            end_dt = end_date or date.today().isoformat()
            if not start_date:
                start_dt = (date.fromisoformat(end_dt) - timedelta(days=365)).isoformat()
            else:
                start_dt = start_date

            d_start = date.fromisoformat(start_dt)
            d_end = date.fromisoformat(end_dt)
            if (d_end - d_start).days > 750:
                return {"error": "Date range too large (max 750 days)"}

            extra_days = 260
            fetch_start = (d_start - timedelta(days=extra_days)).isoformat()

            df = fetcher.fetch_stocks(stocks, fetch_start, end_dt)
            if df is None or df.empty:
                return {"error": "No market data available for this universe/date range"}

            try:
                factor_values = factor_fn(df)
            except Exception as e:
                return {"error": f"Expression evaluation failed: {e}"}

            df["factor_value"] = factor_values
            result_df = df[df["trade_date"] >= start_dt][["trade_date", "stock_code", "factor_value"]].copy()
            result_df = result_df.dropna(subset=["factor_value"])

            dates_data = []
            for trade_date, group in result_df.groupby("trade_date"):
                values = {}
                for _, row in group.iterrows():
                    val = row["factor_value"]
                    if np.isfinite(val):
                        values[row["stock_code"]] = round(float(val), 6)
                if values:
                    dates_data.append({
                        "date": str(trade_date),
                        "values": values,
                        "count": len(values),
                    })

            return {
                "expression": expression,
                "universe": universe,
                "start_date": start_dt,
                "end_date": end_dt,
                "trading_days": len(dates_data),
                "data": dates_data,
            }

    result = await asyncio.to_thread(_compute)
    return json.dumps(result, ensure_ascii=False, default=str)


# Operator documentation fallback
_OPERATORS_DOC = """
因子表达式操作符:

一元函数: rank, zscore, sign, log, abs, scale, tanh, sigmoid, exp, sqrt
时序函数: ts_mean, ts_std, ts_max, ts_min, ts_sum, ts_shift, ts_delta, ts_rank, ts_argmax, ts_argmin, decay_linear, product
  用法: ts_mean(close, 20) — 20日均值
双列时序: ts_corr(col1, col2, N), ts_cov(col1, col2, N)
二元函数: power, max, min
条件函数: clip(expr, lo, hi), where(cond, true_val, false_val)
算术运算: +, -, *, /, ^
比较运算: >, <, >=, <=, ==, !=
特殊变量: vwap, returns, adv{N} (如 adv20)
可用列名: open, high, low, close, volume, amount, pct_change
别名: delta=ts_delta, delay=ts_shift, correlation=ts_corr, covariance=ts_cov
"""
