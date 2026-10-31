"""Daily market summary — factor-driven post-market analysis.

Pipeline:
1. Fetch market data (hs300 stocks, last 70 days) + benchmark returns
2. Compute factor signals from 15 core factor templates
3. Build rich LLM prompt with real factor data
4. Generate markdown report via DeepSeek
5. Store to DB
"""

import json
import logging
import os
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from openai import OpenAI

from .factor_signals import FactorSignal, compute_factor_signals
from .industry_analysis import compute_industry_signals
from .llm_service import get_llm_api_key, get_llm_base_url, get_llm_model
from .market_data import MarketDataFetcher, fetch_benchmark_returns, get_universe
from .market_regime import derive_market_regime

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATES_PATH = Path(__file__).resolve().parent / "templates" / "factors.json"


def _load_factor_templates() -> list:
    """Load factor templates from JSON."""
    with open(_TEMPLATES_PATH, encoding="utf-8") as f:
        return json.load(f)


# ─── Benchmark index changes ─────────────────────────────────────


def _get_today_index_changes(date: str | None = None) -> dict:
    """Fetch benchmark index changes for a given date."""
    today = date or datetime.now().strftime("%Y-%m-%d")
    start = (pd.Timestamp(today) - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    target = pd.Timestamp(today)

    metrics = {}
    for name, code in [("hs300", "hs300"), ("sz50", "sz50"), ("zz500", "zz500"), ("csi1000", "csi1000")]:
        try:
            ret = fetch_benchmark_returns(code, start, today)
            if ret is not None and len(ret) > 0:
                # Use exact date match, not last available
                ret.index = pd.to_datetime(ret.index).normalize()
                if target in ret.index:
                    metrics[f"{name}_change"] = round(float(ret.loc[target]) * 100, 2)
                else:
                    # Fallback to last date, but log warning
                    logger.warning(f"[daily_summary] {name} has no data for {today}, latest is {ret.index[-1].strftime('%Y-%m-%d')}")
                    metrics[f"{name}_change"] = round(float(ret.iloc[-1]) * 100, 2)
            else:
                metrics[f"{name}_change"] = 0.0
        except Exception as e:
            logger.warning(f"Failed to fetch {name} returns: {e}")
            metrics[f"{name}_change"] = 0.0

    return metrics


# ─── LLM prompt building ─────────────────────────────────────────


_CATEGORY_NAMES = {
    "trend": "趋势类",
    "volume": "量价类",
    "volatility": "波动类",
    "technical": "技术类",
    "valuation": "估值类",
}

_SYSTEM_PROMPT = """你是一位资深量化策略师，擅长用因子模型刻画市场结构，并从散户、游资、机构三种视角解读市场行为。

## 核心原则

1. **因子驱动**：所有结论必须基于因子信号数据，禁止凭空推测。
2. **先结论后展开**：每个章节开头用 1-2 句加粗文字给出该段核心结论，方便快速阅读。
3. **因子→经济含义→操作建议**：每个因子不只报数字，要解读"这意味着什么"。
4. **多角色视角**：从散户（情绪/追涨杀跌）、游资（短线博弈/题材轮动）、机构（风格切换/配置调整）角度分析。
5. **信号追踪**：如果提供了近期历史信号，必须对比今日与前几日的信号变化，验证之前建议的有效性，指出信号的延续或反转。

## 排版规范（极其重要，必须严格遵守）

1. **每个 ## 大标题前后必须有一个空行**
2. **每个 ### 小标题前后必须有一个空行**
3. **每个 • 或 - 项目符号条目必须独占一行，条目之间用空行分隔**
4. **段落之间必须有空行，禁止文字紧贴**
5. 不要用 • 符号，统一用 Markdown 的 - 项目符号
6. 每个因子解读必须独占一段，格式为：`- **因子名（方向）：** 解读内容`，每个因子之间空一行

## 严格要求

1. 直接输出正文，第一行必须是 `#` 标题，禁止任何开场白（"好的""根据"等）
2. 严禁出现任何个股代码或个股名称，只能用"多头组Top 10%""分位90%"等分组表达
3. 使用 **加粗** 突出关键数字和结论
4. 每个因子的解读必须用 Markdown 无序列表（`- ` 开头），禁止直接写成段落
5. 字数控制在 1800-2500 字
6. 不要编造数据中没有的信息
7. 重点解读分位数异常（>80 或 <20）和 Z-Score 异常（|z|>1.5）的因子
8. 行业轮动分析必须基于提供的行业因子数据，给出具体行业名称和因子逻辑
9. 报告末尾必须有总结+投资建议+免责声明"""


def _build_llm_prompt(
    date: str,
    index_changes: dict,
    factor_signals: list[FactorSignal],
    regime_data: dict | None = None,
    industry_signals: list[dict] | None = None,
    history_summaries: list[dict] | None = None,
) -> str:
    """Build a rich LLM prompt with real factor data (no individual stock codes)."""
    lines = [f"今日日期：{date}\n"]

    # Market regime context (computed from factors, not price)
    if regime_data:
        lines.append("## 市场状态（由因子信号推导，非价格反推）\n")
        lines.append(f"- **Regime**: {regime_data.get('regime', '未知')}")
        lines.append(f"- **风格**: {regime_data.get('style', '未知')}")
        lines.append(f"- **风险等级**: {regime_data.get('risk_level', '未知')}")
        lines.append(f"- **一句话**: {regime_data.get('headline', '')}")
        lines.append("")

    # Historical signal tracking (past 3 days)
    if history_summaries:
        lines.append("## 近期信号回顾（用于验证和追踪）\n")
        for hist in history_summaries:
            h_date = hist["date"]
            h_metrics = hist["metrics"]
            h_signals = h_metrics.get("factor_signals", [])
            h_industries = h_metrics.get("industry_signals", [])
            h_regime = h_metrics.get("headline", "")

            lines.append(f"### {h_date}")
            if h_regime:
                lines.append(f"**市场状态**: {h_regime}")

            # Factor signal summary
            if h_signals:
                h_up = sum(1 for s in h_signals if s.get("direction") == "转强")
                h_down = sum(1 for s in h_signals if s.get("direction") == "转弱")
                signal_parts = []
                for s in h_signals:
                    d = s.get("direction", "持平")
                    if d != "持平":
                        signal_parts.append(f"{s.get('factor_name', '')}({d})")
                lines.append(f"**因子**: {h_up}强 {h_down}弱 — {', '.join(signal_parts[:6])}")

            # Top/bottom industries
            if h_industries:
                top3 = [i["industry"] for i in h_industries[:3]]
                bot3 = [i["industry"] for i in h_industries[-3:]]
                lines.append(f"**强势行业**: {', '.join(top3)} | **弱势行业**: {', '.join(bot3)}")

            # Extract key recommendations from past content (last section)
            h_content = hist.get("content", "")
            # Find investment advice section
            advice_lines = []
            in_advice = False
            for cl in h_content.split("\n"):
                if "投资建议" in cl or "操作建议" in cl:
                    in_advice = True
                    continue
                if in_advice and cl.strip().startswith("- **"):
                    advice_lines.append(cl.strip())
                if in_advice and cl.strip().startswith("#"):
                    break
            if advice_lines:
                lines.append(f"**当日建议**: {' | '.join(a[:40] for a in advice_lines[:3])}")

            lines.append("")

    # Index changes
    lines.append("## 大盘数据\n")
    lines.append("| 指数 | 涨跌幅 |")
    lines.append("|------|--------|")
    lines.append(f"| 沪深300 | {index_changes.get('hs300_change', 0)}% |")
    lines.append(f"| 上证50 | {index_changes.get('sz50_change', 0)}% |")
    lines.append(f"| 中证500 | {index_changes.get('zz500_change', 0)}% |")
    lines.append(f"| 中证1000 | {index_changes.get('csi1000_change', 0)}% |")
    lines.append("")

    # Factor signal summary
    up_count = sum(1 for s in factor_signals if s.direction == "转强")
    down_count = sum(1 for s in factor_signals if s.direction == "转弱")
    flat_count = sum(1 for s in factor_signals if s.direction == "持平")
    lines.append("## 因子信号总览（基于沪深300成分股）\n")
    lines.append(f"**{up_count}** 个因子转强，**{down_count}** 个转弱，**{flat_count}** 个持平\n")

    # Factor signals grouped by category — NO individual stock codes
    category_order = ["trend", "volume", "volatility", "technical", "valuation"]
    for cat in category_order:
        cat_signals = [s for s in factor_signals if s.category == cat]
        if not cat_signals:
            continue

        lines.append(f"### {_CATEGORY_NAMES.get(cat, cat)}")
        for s in cat_signals:
            lines.append(
                f"- **{s.factor_name}** [{s.direction}] 强度{s.signal_strength:+d}，"
                f"20日分位{s.percentile_20d:.0f}%，Z-Score={s.zscore_20d:+.2f}，"
                f"分化度[{s.dispersion}]，"
                f"多头组Top10%日均变化{s.top10_pct_change:+.4f}，"
                f"{s.pct_above_median}%标的高于中位数。"
                f"{s.signal_description}"
            )
        lines.append("")

    # Industry rotation signals
    if industry_signals:
        n_top = min(5, len(industry_signals))
        n_bot = min(5, len(industry_signals))
        top_industries = industry_signals[:n_top]
        bottom_industries = industry_signals[-n_bot:]

        lines.append("## 行业轮动信号（基于沪深300成分股，申万一级行业）\n")

        lines.append("### 强势行业（综合因子得分 Top 5）")
        for ind in top_industries:
            factors_str = "、".join(
                f"{f['name']}({f['today_mean'] or 0:+.4f})"
                for f in ind["top_factors"][:3]
            )
            lines.append(
                f"- **{ind['industry']}**（{ind['stock_count']}只）"
                f"综合得分{ind['composite_score']:+.2f}[{ind['direction']}]："
                f"{factors_str}"
            )
        lines.append("")

        lines.append("### 弱势行业（综合因子得分 Bottom 5）")
        for ind in reversed(bottom_industries):
            factors_str = "、".join(
                f"{f['name']}({f['today_mean'] or 0:+.4f})"
                for f in ind["top_factors"][:3]
            )
            lines.append(
                f"- **{ind['industry']}**（{ind['stock_count']}只）"
                f"综合得分{ind['composite_score']:+.2f}[{ind['direction']}]："
                f"{factors_str}"
            )
        lines.append("")

    # Writing instructions
    lines.append("## 输出格式要求\n")
    lines.append("严格按以下结构输出，不要有任何开场白：\n")
    lines.append(f"# A股市场量化研究日报 | {date}\n")
    lines.append("## 一、市场全景解读\n")
    lines.append('**开头用 1-2 句加粗文字给出今日市场核心特征**（如“这是一个典型的小盘风格占优交易日”）。\n')
    lines.append("然后用编号列表展开：")
    lines.append("1. 行情特征：指数排序（如 中证1000 > 中证500 > 沪深300），说明大小盘分化")
    lines.append("2. 解读：结合趋势因子方向，判断市场风险偏好（Risk-on/Risk-off）")
    lines.append("3. 资金面：结合量价因子判断资金活跃度和流向\n")

    if history_summaries:
        lines.append("## 一点五、信号追踪与验证\n")
        lines.append("**基于上方提供的近期信号回顾数据，完成以下分析：**\n")
        lines.append("1. **建议验证**：前几日给出的投资建议，今天市场是否验证了？哪些建议命中了，哪些失效了？")
        lines.append("2. **信号延续性**：哪些因子信号连续多天保持同一方向？连续信号比单日信号更可靠")
        lines.append("3. **信号反转**：哪些因子今天方向发生了反转？反转意味着什么？")
        lines.append("4. **行业轮动追踪**：哪些行业连续多天出现在强势/弱势名单中？新进入的行业值得关注")
        lines.append("注意：如果没有历史数据则跳过此章节\n")

    lines.append("## 二、核心因子信号深度拆解\n")
    lines.append('**开头用 1 句话概括因子信号全貌**（如“本报告通过X个维度监测了市场背后的逻辑”）\n')
    lines.append("按类别逐一解读，每个类别：")
    lines.append("1. 类别小标题用 `### 1. 趋势类：xxx` 格式，冒号后用 3-5 字概括该类因子传递的信号")
    lines.append("2. 每个因子必须用 Markdown 列表格式（`- `开头），每个因子独占一段，格式：")
    lines.append("   `- **因子名（方向）：** 经济含义解读`")
    lines.append('   - 不只报数字，要解释“这意味着什么”')
    lines.append("   - 例：")
    lines.append('   - **20日动量（转强）：** 意味着“强者恒强”。加速动量显著转强(+0.27)，说明市场不仅在涨，涨速还在加快，这是情绪进入高潮的标志。')
    lines.append('   - 例：**5日反转（转弱）：** 意味着跌了去抄底的逻辑行不通了，现在的市场逻辑是“追高”。')
    lines.append("3. 每类因子解读完后，用 1 句话总结该类因子传递的整体信号\n")

    lines.append("## 三、参与者行为画像\n")
    lines.append("**开头用 1 句话概括当前市场的参与者结构**\n")
    lines.append("从三个角度分析：")
    lines.append("1. **散户视角**：基于动量/反转因子判断追涨杀跌情绪，换手率异动判断散户参与热度")
    lines.append("2. **游资视角**：基于成交量异动+日内动量判断短线博弈强度，高分化因子判断题材轮动")
    lines.append("3. **机构视角**：基于低波动+均线偏离+估值因子判断机构风格切换和配置调整\n")

    if industry_signals:
        lines.append("## 四、行业轮动与板块建议\n")
        lines.append("**开头用 1 句话概括今日行业轮动格局**\n")
        lines.append("基于上方提供的行业因子数据，分析：")
        lines.append("1. **强势板块解读**：逐一解读 Top 3-5 强势行业，说明哪些因子驱动了该行业走强，经济含义是什么")
        lines.append("2. **弱势板块解读**：逐一解读 Bottom 3-5 弱势行业，说明弱势原因")
        lines.append("3. **板块轮动建议**：基于因子信号给出 2-3 条具体的板块配置建议")
        lines.append("   - 例：**关注电子板块：** 动量转强+成交量异动+价格突破三因子共振，资金持续流入")
        lines.append("   - 例：**回避银行板块：** 低波动因子偏弱+动量转弱，机构可能在减配\n")

        lines.append("## 五、总结与操作建议\n")
    else:
        lines.append("## 四、总结与操作建议\n")
    lines.append("### 1. 核心结论")
    lines.append('用 2-3 句话总结今日市场全貌，提炼关键词（如“情绪驱动”“中小盘领头”“动能加速”等）\n')
    lines.append("### 2. 投资建议")
    lines.append("用项目符号列出 3-4 条**基于因子逻辑的具体建议**：")
    lines.append("- 每条建议格式：**建议标题：** 具体说明 + 因子逻辑支撑")
    lines.append("- 例：**不要轻易抄底：** 因为反转因子转弱，之前热度较高的股票跌下去后短期内起不来。")
    lines.append("- 例：**关注低位补涨：** 换手率反转因子转强提示，寻找处于底部、近期刚开始活跃的中小市值品种。")
    lines.append("- 例：**注意止盈：** 均线偏离度显示超买，不建议此时大幅加仓，应在普涨中逐步兑现远离均线的盈利筹码。\n")

    lines.append("---")
    lines.append("*本内容基于量化因子模型与历史数据分析，仅供研究参考，不构成任何投资建议。市场有风险，投资需谨慎。*")

    return "\n".join(lines)


# ─── LLM call ─────────────────────────────────────────────────────


def _call_llm(prompt: str) -> str:
    """Call the configured LLM for market summary."""
    client = OpenAI(api_key=get_llm_api_key(), base_url=get_llm_base_url())
    resp = client.chat.completions.create(
        model=get_llm_model(),
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.4,
        max_tokens=5000,
    )
    text = (resp.choices[0].message.content or "").strip()

    # Strip any preamble before the first markdown heading
    match = re.search(r'^(#{1,3}\s)', text, re.MULTILINE)
    if match and match.start() > 0:
        text = text[match.start():]

    # Post-process: ensure blank lines around headings and between list items
    text = _fix_markdown_spacing(text)

    return text


def _fix_markdown_spacing(text: str) -> str:
    """Ensure proper blank lines in markdown output for readable rendering."""
    lines = text.split("\n")
    result = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        prev_stripped = result[-1].strip() if result else ""

        # Ensure blank line before headings (# ## ###)
        if stripped.startswith("#") and prev_stripped and not prev_stripped == "":
            result.append("")

        result.append(line)

        # Ensure blank line after headings
        if stripped.startswith("#") and i + 1 < len(lines) and lines[i + 1].strip():
            result.append("")

    text = "\n".join(result)

    # Collapse 3+ consecutive blank lines to 2
    text = re.sub(r'\n{4,}', '\n\n\n', text)

    return text


# ─── Main pipeline ────────────────────────────────────────────────


async def generate_daily_summary(db, market: str = "a_share", date: str | None = None) -> dict | None:
    """Generate and store daily market summary using factor signals.

    Args:
        db: async DB session.
        market: "a_share".
        date: target date "YYYY-MM-DD". Defaults to today.

    Returns the summary dict or None if already exists for that date.
    """
    import uuid

    from sqlalchemy import desc, select

    from .models import DailySummary

    today = date or datetime.now().strftime("%Y-%m-%d")

    # Check if already generated
    existing = await db.execute(
        select(DailySummary).where(
            DailySummary.date == today,
            DailySummary.market == market,
        )
    )
    if existing.scalar_one_or_none():
        logger.info(f"Daily summary for {today} ({market}) already exists, skipping")
        return None

    logger.info(f"[daily_summary] Starting factor-driven summary for {today} ({market})")

    # Step 1: Get index changes
    index_changes = _get_today_index_changes(today)
    logger.info(f"[daily_summary] Index changes: {index_changes}")

    # Step 2: Load market data for factor computation (last 70 trading days)
    start_date = (pd.Timestamp(today) - pd.Timedelta(days=100)).strftime("%Y-%m-%d")
    try:
        stock_codes = get_universe("hs300", date=today)
        fetcher = MarketDataFetcher()
        market_df = fetcher.fetch_stocks(stock_codes, start_date, today)
    except Exception as e:
        logger.error(f"[daily_summary] Failed to fetch market data: {e}")
        market_df = None

    # Step 3: Load templates and compute factor signals
    factor_signals = []
    if market_df is not None and len(market_df) > 0:
        templates = _load_factor_templates()

        # Check if valuation factors need fundamental data
        valuation_templates = [t for t in templates if t["category"] == "valuation"]
        if valuation_templates:
            try:
                from .fundamental_data import detect_fundamental_vars, enrich_with_fundamentals_rq
                all_fund_vars = set()
                for t in valuation_templates:
                    all_fund_vars |= detect_fundamental_vars(t["expression"])
                if all_fund_vars:
                    enriched = enrich_with_fundamentals_rq(
                        market_df, all_fund_vars, stock_codes, start_date, today
                    )
                    if enriched is not None:
                        market_df = enriched
                        logger.info(f"[daily_summary] Enriched with fundamentals: {all_fund_vars}")
                    else:
                        logger.warning("[daily_summary] Fundamental data unavailable, skipping valuation factors")
                        templates = [t for t in templates if t["category"] != "valuation"]
            except Exception as e:
                logger.warning(f"[daily_summary] Fundamental enrichment failed: {e}")
                templates = [t for t in templates if t["category"] != "valuation"]

        factor_signals = compute_factor_signals(market_df, templates)
        logger.info(f"[daily_summary] Computed {len(factor_signals)} factor signals")
    else:
        logger.warning("[daily_summary] No market data, generating summary with index data only")

    # Step 3b: Compute industry-level signals
    industry_signals = []
    if market_df is not None and len(market_df) > 0 and templates:
        try:
            industry_signals = compute_industry_signals(market_df, templates)
        except Exception as e:
            logger.warning(f"[daily_summary] Industry signal computation failed: {e}")

    # Step 4: Derive market regime from factor signals
    regime_data = derive_market_regime(factor_signals, index_changes)
    if regime_data:
        logger.info(f"[daily_summary] Regime: {regime_data.get('headline', '')}")

    # Step 4b: Fetch past 3 days' summaries for signal tracking
    history_summaries = []
    try:
        past_start = (pd.Timestamp(today) - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
        hist_result = await db.execute(
            select(DailySummary).where(
                DailySummary.date >= past_start,
                DailySummary.date < today,
                DailySummary.market == market,
            ).order_by(desc(DailySummary.date)).limit(3)
        )
        for s in hist_result.scalars().all():
            history_summaries.append({
                "date": s.date,
                "metrics": s.metrics or {},
                "content": s.content or "",
            })
        logger.info(f"[daily_summary] Loaded {len(history_summaries)} historical summaries")
    except Exception as e:
        logger.warning(f"[daily_summary] Failed to load history: {e}")

    # Step 5: Build prompt and call LLM
    prompt = _build_llm_prompt(today, index_changes, factor_signals, regime_data, industry_signals, history_summaries)
    logger.info(f"[daily_summary] LLM prompt: {len(prompt)} chars, calling DeepSeek...")
    content = _call_llm(prompt)
    logger.info(f"[daily_summary] LLM response: {len(content)} chars")

    # Step 6: Store in DB
    metrics = {
        **index_changes,
        **regime_data,
        "factor_signals": [asdict(s) for s in factor_signals],
        "factor_count": len(factor_signals),
        "industry_signals": industry_signals,
    }

    summary = DailySummary(
        id=uuid.uuid4(),
        date=today,
        market=market,
        title=f"{today} A股盘后总结",
        content=content,
        metrics=metrics,
        created_at=datetime.now(timezone.utc),
    )
    db.add(summary)
    await db.commit()

    logger.info(f"[daily_summary] Summary for {today} saved successfully")
    return {
        "id": str(summary.id),
        "date": summary.date,
        "title": summary.title,
        "content": summary.content,
        "metrics": summary.metrics,
    }
