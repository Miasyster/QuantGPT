"""LLM integration — DeepSeek API calls for factor expression generation and interpretation."""

import json
import logging
import os
import re

from .expression_parser import __doc__ as _expr_module_doc

logger = logging.getLogger(__name__)


_FACTOR_OPERATORS = """
================================================================================
Factor Expression Syntax (Alpha101+ Extended)
================================================================================

SUPPORTED OPERATORS:

Cross-sectional: rank(expr), zscore(expr), sign(expr), log(expr), abs(expr), scale(expr)
Time-series: ts_mean(col,N), ts_std(col,N), ts_sum(col,N), ts_max(col,N), ts_min(col,N),
  ts_shift(col,N), ts_delta(col,N), ts_rank(col,N), ts_argmax(col,N), ts_argmin(col,N),
  decay_linear(col,N), product(col,N)
Technical indicators: ema(col,N), sma(col,N), wma(col,N), rsi(col,N), macd(col,N),
  boll_upper(col,N), boll_lower(col,N), boll_mid(col,N), obv(col,N), atr(N)
Dual-column: ts_corr(col1,col2,N), ts_cov(col1,col2,N)
Nonlinear: power(base,exp), sign_power(base,exp), tanh(expr), sigmoid(expr), exp(expr), sqrt(expr)
Conditional: max(a,b), min(a,b), where(cond,true_val,false_val), clip(expr,lower,upper)
Arithmetic: +, -, *, /, ^ (power)
Comparison: >, <, >=, <=, ==, !=
Logical: and, or (combine conditions in where())
Columns: open, high, low, close, volume, amount, pct_change
Special vars: vwap, adv{N} (e.g. adv20), returns, cap
Fundamental (精确变量名，不可用其他别名):
  盈利: roe, np_margin, gp_margin, net_profit, eps_ttm, revenue
  股本: total_share, float_share
  成长: yoy_ni, yoy_equity, yoy_asset, yoy_pni
  偿债: current_ratio, debt_ratio, equity_multiplier
  运营: asset_turnover, inv_turnover, dupont_roe, dupont_asset_turn
  现金流: cfo_to_np
  估值(衍生): pe, pb, ps, roa, bps, nav, dividend_yield
  ⚠️ 禁止使用的变量(会导致报错): pe_ratio, pe_ttm, pb_ratio, ps_ratio, roe_avg
Aliases: delta=ts_delta, delay=ts_shift, correlation=ts_corr, covariance=ts_cov

================================================================================
SYNTAX RULES:
================================================================================
RULE #1: 每个时序函数需要正确的参数个数
  ts_mean(col, N) — 2 个参数    ts_corr(col1, col2, N) — 3 个参数
  where(cond, true_val, false_val) — 3 个参数
  ✗ ts_shift(expr < 30, 1, ...) ← 错误，ts_shift 只接受 2 个参数

RULE #2: 括号必须严格平衡
  ✓ rank(close / ts_mean(close, 20))
  ✗ rank(close / ts_mean(close, 20) ← 缺少右括号

RULE #3: where() 条件可以用 and/or 组合多个条件
  ✓ where(close > ts_mean(close, 5) and volume > ts_mean(volume, 10), close, 0)
  ✓ where(ts_rank(volume, 20) > 0.7 or ts_delta(close, 5) > 0, 1, 0)

RULE #4: 使用非线性变换捕捉市场动态
  ✓ power(rank(volume/adv20), 2)
  ✓ sign_power(ts_corr(close, volume, 20), 0.5)
  ✓ log(1 + abs(ts_delta(close, 20)/close)) * sign(ts_delta(close, 20))

RULE #5: 组合多种信号类型
  ✓ rank(ts_corr(close, volume, 20)) * rank(ts_delta(close, 10)/close)

================================================================================
EXAMPLES:
================================================================================
动量: rank(close/ts_mean(close, 20))
反转: rank(-1 * ts_delta(close, 5) / ts_shift(close, 5))
波动率: ts_std(close/ts_shift(close, 1) - 1, 20)
量价相关: rank(ts_corr(close, volume, 10))
成交量异动: rank(volume/ts_mean(volume, 10))
非线性动量: sign_power(ts_delta(close, 20)/close, 0.5) * rank(volume/adv20)
条件因子: rank(where(ts_rank(volume,20) > 0.7, ts_delta(close,10)/close, 0)) * rank(volume/adv20)
多头排列: rank(where(close > ts_mean(close, 5) and ts_mean(close, 5) > ts_mean(close, 10), close / ts_mean(close, 20), 0))
衰减加权: decay_linear(rank(ts_corr(vwap, volume, 10)), 5)
VWAP偏离(WQ BRAIN近A级): -1 * rank(ts_decay_linear(close / vwap, 5))
复合因子: sign_power(rank(volume/adv20), 2) * rank((close-vwap)/close) * rank(ts_std(returns,20))
裁剪因子: rank(clip(ts_corr(close, volume, 20), -0.5, 0.5)) * sign_power(ts_delta(close,20)/close, 0.5)
价值因子: rank(-1 * pe)
质量因子: rank(roe * asset_turnover)
成长因子: rank(yoy_ni)
基本面+动量: rank(roe) * rank(ts_delta(close, 20) / ts_shift(close, 20))
高股息: rank(dividend_yield) * rank(-1 * ts_std(returns, 20))
技术指标-RSI: rank(-1 * rsi(close, 14))
技术指标-MACD: rank(macd(close, 26))
技术指标-布林带: rank((close - boll_lower(close, 20)) / (boll_upper(close, 20) - boll_lower(close, 20) + 1e-10))
技术指标-EMA动量: rank(ema(close, 5) / ema(close, 20) - 1)
技术指标-ATR波动: rank(-1 * atr(14) / close)
================================================================================
"""

OPERATORS_DOC = _FACTOR_OPERATORS

_SYSTEM_PROMPT = """你是一个量化因子表达式生成器。用户会用自然语言描述想要的因子，你需要生成一个合法的因子表达式。

{operators}

================================================================================
⚠️ 关键注意事项
================================================================================
- 🚨 只能使用上面 SUPPORTED OPERATORS 中列出的函数，禁止使用 bbands, adx 等未列出的函数
- 🚨 技术指标已支持：ema(col,N) EMA, sma(col,N) 简单均线, wma(col,N) 加权均线, rsi(col,N) RSI(0~100), macd(col,N) MACD柱状图, atr(N) 真实波幅(用high/low/close), boll_upper/boll_lower/boll_mid(col,N) 布林带, obv(col,N) OBV滚动和
- 🚨 变量名必须严格匹配：pe_ratio→pe, pe_ttm→pe, pb_ratio→pb, eps→eps_ttm, div_yield→dividend_yield
- 🚨 如果用户要求的指标不在支持列表中，用最接近的已支持变量替代，并在表达式中注释说明
- ts_rank(col, N) 返回百分位排名，范围 0~1（不是 0~100），与之比较时用 0.3 而非 30
- where() 条件会使因子值变成离散值（如 -1, 0, 1），可能导致分组失败，尽量避免使用
- 优先使用连续值因子表达式（如 rank(), zscore(), ts_mean() 等），分组效果更好
- returns 是日收益率（等同于 pct_change，如 0.02 代表 2%），close 是收盘价
- day/weekday/month 是日期特殊变量，仅在用户明确要求日历效应时使用
- 基本面变量(roe, pe, yoy_ni 等)是季度财报按发布日对齐到日频的，变化较慢
- 估值因子通常取负值排序(低估值更好)：rank(-1 * pe)
- 推荐将基本面与价量信号组合：rank(roe) * rank(ts_delta(close, 20)/close)

================================================================================
🎯 因子质量指南（非常重要）
================================================================================
简单单因子（如 rank(ts_delta(close, 20))）通常 Sharpe < 0.3，效果很差。
请优先生成**多信号复合因子**，结合不同维度的信息：

高质量因子设计原则：
1. 多维度组合：结合价格动量 + 成交量 + 波动率等至少2个维度
2. 非线性变换：使用 sign_power, tanh, sigmoid 捕捉非线性关系
3. 多周期信号：组合短期(5日)和中期(20日)信号，捕获不同频率
4. 截面标准化：最外层用 rank() 或 zscore() 保证因子截面可比
5. 适度复杂度：3-6层嵌套为宜，避免过度简单也避免过度复杂

避免生成以下低效因子：
- 仅包含单一算子的简单因子：rank(close), rank(ts_delta(close, 20))
- 仅调整窗口参数的同质因子：ts_mean(close, 5) - ts_mean(close, 20)
- 纯离散型因子（大量使用 where 生成 -1/0/1 值）

================================================================================
📈 WorldQuant Fitness 优化策略
================================================================================
Fitness = Sharpe × √(|Returns| / max(Turnover, 0.125))
三个杠杆：高 Sharpe、高绝对收益、低换手率。

提升 Fitness 的关键技巧（按优先级排列）：
1. ts_decay_linear() 包裹最终信号：ts_decay_linear(rank(signal), 5~10)，平滑信号降低换手率
2. rank() 是最关键算子：将原始信号转为百分位，消除量纲偏差，提升单调性
3. ts_zscore(col, N) 取代原始值：ts_zscore(pe, 63) 捕捉变化而非水平
4. 双重排名（时序+截面）：rank(ts_rank(col, 40)) 双重过滤提高稳定性
5. 相关性过滤：-ts_av_diff(x, 50) * ts_corr(x, y, 50) 仅在结构性有效时入场
6. trade_when 波动率门控：trade_when(vol<threshold, signal, 0) 大幅降低换手率

高 Fitness 模板（可直接使用或改编）：
- ts_decay_linear(rank((vwap-close)/close), 5)                 — Fitness ~2.86
- rank(ts_rank(close/ts_shift(close,5)-1, 40))                 — Fitness ~1.42-1.58
- -ts_av_diff(close, 50) * ts_corr(close, volume, 50)          — Fitness ~1.70
- -rank(ts_zscore(pe, 63))                                     — Fitness ~1.26-1.70
- trade_when(ts_std(returns,20)<threshold, rank(signal), 0)    — 最强降换手算子

================================================================================
🚨 输出格式要求（必须严格遵守）🚨
================================================================================
只返回一个因子表达式，不要任何解释、分析或推理过程。
不要使用 markdown 代码块、反引号或引号包裹。
不要以"根据分析"、"我将"、"改进的因子"等开头。

✅ 正确（你的完整回复）:
rank(volume / ts_mean(volume, 20))

❌ 错误（会导致执行失败）:
根据分析，我建议使用反转因子：
rank((close - ts_mean(close, 60)) / ts_std(close, 60))

你的回复必须是恰好一行可执行的因子表达式，不要任何其他内容。
================================================================================
"""


def clean_expression(raw: str) -> str:
    """Clean LLM response to extract pure factor expression."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    text = text.strip("`").strip()
    if "\n" in text:
        factor_ops = ["rank(", "ts_mean(", "ts_std(", "ts_delta(", "ts_shift(",
                       "ts_corr(", "where(", "sign_power(", "power(", "decay_linear(",
                       "log(", "abs(", "zscore(", "close", "volume"]
        for line in reversed(text.split("\n")):
            line = line.strip()
            if any(op in line for op in factor_ops):
                return line
    return text


def validate_parentheses(expr: str) -> str | None:
    """Check if parentheses are balanced. Returns error message or None."""
    depth = 0
    for i, ch in enumerate(expr):
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth < 0:
                return f"括号不平衡：位置 {i} 处多余的右括号 ')'"
    if depth > 0:
        return f"括号不平衡：缺少 {depth} 个右括号 ')'"
    return None


def get_llm_api_key() -> str:
    """LLM_* takes precedence; DEEPSEEK_* is kept for existing configurations."""
    return os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or ""


def get_llm_base_url() -> str:
    return os.environ.get("LLM_BASE_URL") or os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com/v1"


def get_llm_model() -> str:
    return os.environ.get("LLM_MODEL") or os.environ.get("DEEPSEEK_MODEL") or "deepseek-chat"


def _get_client():
    from openai import OpenAI
    api_key = get_llm_api_key()
    if not api_key:
        raise RuntimeError("LLM_API_KEY environment variable is not set")
    return OpenAI(api_key=api_key, base_url=get_llm_base_url())


def _get_model() -> str:
    return get_llm_model()


def call_deepseek(prompt: str) -> str:
    """Call DeepSeek API to generate factor expression."""
    client = _get_client()
    operators_doc = _expr_module_doc or _FACTOR_OPERATORS
    system = _SYSTEM_PROMPT.format(operators=operators_doc)

    resp = client.chat.completions.create(
        model=_get_model(),
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
        max_tokens=256,
        timeout=30,
    )
    return clean_expression(resp.choices[0].message.content)


def call_fix_expression(expression: str, error: str, prompt: str) -> str:
    """Call LLM to fix a broken factor expression."""
    client = _get_client()
    operators_doc = _expr_module_doc or _FACTOR_OPERATORS

    system = (
        "你是一个因子表达式修复助手。\n\n"
        f"{operators_doc}\n\n"
        "修复下面的表达式。只返回修正后的表达式，不要任何解释、代码块或引号。"
    )
    user = (
        f"用户需求: {prompt}\n\n"
        f"以下因子表达式执行失败:\n"
        f"`{expression}`\n\n"
        f"错误信息:\n{error}"
    )

    resp = client.chat.completions.create(
        model=_get_model(),
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.1,
        max_tokens=256,
        timeout=30,
    )
    return clean_expression(resp.choices[0].message.content)


_INTERPRET_SYSTEM = """你是一位专业的量化研究员，擅长用通俗语言解读因子表达式的经济含义并撰写研究报告。

你的任务是解读一个因子表达式，输出 JSON，格式如下：
{
  "logic": "因子的核心逻辑（1-2句，说明该因子捕捉了什么市场现象）",
  "source": "收益来源（1-2句，说明为什么这个因子能产生超额收益，背后的行为金融或基本面逻辑）",
  "guidance": "交易指导（2-4句，从经济含义角度指导用户如何利用该因子思路交易，禁止推荐具体标的，聚焦行为规律和风险提示）",
  "risk": "主要风险（1句，说明该因子在什么市场环境下容易失效）",
  "conclusion": "核心结论（2-3句，总结因子整体表现和是否推荐使用）",
  "suggestions": ["改进建议1", "改进建议2"]
}

注意：评级(rating)由系统算法自动生成，你不需要输出评级。

交易指导要求：
- 禁止推荐任何具体标的
- 从行为金融角度出发，指出市场参与者的非理性行为
- 结合回测指标（如换手率、IC、单调性）给出实操建议
- 语言简洁，面向普通投资者

只输出 JSON，不要任何额外文字。"""


def call_interpret_factor(
    expression: str,
    prompt: str,
    metrics: dict,
    backtest_summary: dict,
) -> dict:
    """Call LLM to interpret factor economic meaning."""
    if not get_llm_api_key():
        return {}

    try:
        client = _get_client()
    except RuntimeError:
        return {}

    sharpe = metrics.get("sharpe", 0)
    cagr = metrics.get("cagr", 0)
    max_dd = metrics.get("max_drawdown", 0)
    ic = backtest_summary.get("ic_mean", 0)
    rank_ic = backtest_summary.get("rank_ic_mean", 0)
    mono = backtest_summary.get("monotonicity_score", 0)
    turnover = backtest_summary.get("turnover", 0)

    user_msg = (
        f"用户需求：{prompt}\n"
        f"因子表达式：{expression}\n\n"
        f"回测指标（供参考）：\n"
        f"- 年化收益：{cagr*100:.1f}%，Sharpe：{sharpe:.2f}，最大回撤：{max_dd*100:.1f}%\n"
        f"- IC均值：{ic:.4f}，Rank IC：{rank_ic:.4f}，单调性：{mono:.2f}，换手率：{turnover*100:.1f}%\n"
    )

    try:
        resp = client.chat.completions.create(
            model=_get_model(),
            messages=[
                {"role": "system", "content": _INTERPRET_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.3,
            max_tokens=600,
            timeout=30,
        )
        raw = (resp.choices[0].message.content or "").strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw)
    except Exception as e:
        logger.warning(f"Factor interpretation failed: {e}")
        return {}


_EXPR_KEYWORDS = re.compile(
    r'(?:rank|zscore|ts_mean|ts_std|ts_delta|ts_shift|ts_rank|ts_corr|ts_cov|'
    r'ts_max|ts_min|ts_sum|ts_argmax|ts_argmin|decay_linear|product|sign_power|'
    r'where|clip|log|abs|sign|scale|tanh|sigmoid|exp|sqrt|power)\s*\('
)


def looks_like_expression(text: str) -> bool:
    """Heuristic: does the text look like a factor expression rather than natural language?"""
    if _EXPR_KEYWORDS.search(text):
        return True
    from .fundamental_data import ALL_FUNDAMENTAL_NAMES as _FN
    cols = {'open', 'high', 'low', 'close', 'volume', 'amount', 'returns', 'vwap'} | _FN
    tokens = re.findall(r'[a-zA-Z_]\w*', text)
    if tokens and all(t in cols for t in tokens):
        return True
    return False
