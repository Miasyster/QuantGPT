"""Factor iteration optimization — QuantGPT
Copyright (c) 2026 Miasyster. Licensed under the MIT License.
https://github.com/Miasyster/QuantGPT

Scoring, prompt building, candidate generation.

Refactored with QuantaAlpha three-phase evolution architecture:
  Phase 1: TrajectoryAnalyzer — trajectory quality metrics
  Phase 2: MetaEvolutionSelector — adaptive strategy selection
  Phase 3: Strategy execution (Mutation / Crossover / Explore)
"""

import hashlib
import logging
import os
import re
import traceback
from pathlib import Path
from typing import Callable

import pandas as pd

from .crossover_engine import build_crossover_prompt, extract_top_segments
from .expression_parser import parse_expression
from .meta_evolution import EvolutionStrategy, select_strategy
from .mutation_engine import MutationEngine
from .report import generate_report
from .task_executor import _run_backtest_in_process, get_executor
from .trajectory_analyzer import analyze_trajectory

logger = logging.getLogger(__name__)


# ---- Factor scoring (unchanged) ----

def compute_factor_score(
    backtest_summary: dict,
    report_metrics: dict,
    anti_overfit_score: float | None = None,
    data_days: int | None = None,
) -> dict:
    """Compute a composite 0-100 score for a factor backtest result.

    6-component scoring with Cloud alignment as primary external target:
      IC Mean 15%, IC IR 15%, Stability 15%, Anti-Overfit 15%,
      Group BT 15%, Cloud Alignment 25%.
    """
    def _clamp(v, lo, hi):
        return max(lo, min(hi, v))

    ic_mean = backtest_summary.get("ic_mean", 0.0) or backtest_summary.get("rank_ic_mean", 0.0)
    ic_mean_score = min(abs(ic_mean) / 0.05, 1.0) * 100

    ic_ir = backtest_summary.get("ic_ir", 0.0)
    ic_ir_score = min(abs(ic_ir) / 1.0, 1.0) * 100

    ic_win_rate = backtest_summary.get("ic_win_rate", 0.5)
    ic_wr_sub = min(max(ic_win_rate - 0.5, 0) / 0.2, 1.0) * 100
    ls_sharpe = backtest_summary.get("long_short_sharpe", 0.0)
    ls_consistency_sub = min(abs(ls_sharpe) / 2.0, 1.0) * 100
    stability_score = ic_wr_sub * 0.6 + ls_consistency_sub * 0.4

    ao_score = _clamp(anti_overfit_score, 0, 100) if anti_overfit_score is not None else 50.0

    ls_sharpe_gb = min(max(ls_sharpe, 0) / 1.0, 1.0) * 100
    mono = backtest_summary.get("monotonicity_score", 0.0)
    mono_sub = _clamp(mono, 0, 1) * 100
    spread = backtest_summary.get("spread", 0.0)
    top_positive_sub = 100.0 if spread > 0 else 0.0
    group_bt_score = ls_sharpe_gb * 0.4 + mono_sub * 0.4 + top_positive_sub * 0.2

    # Cloud Alignment: IC Mean (30%) + IC IR (30%) + Turnover (20%) + Data Sufficiency (20%)
    cloud_ic_mean = abs(ic_mean)
    cloud_ic_ir = abs(ic_ir)
    cloud_turnover = backtest_summary.get("turnover", 0.0)
    cloud_data_days = data_days if data_days is not None else 120

    cloud_ic_mean_sub = min(cloud_ic_mean / 0.03, 1.0) * 100
    cloud_ic_ir_sub = min(cloud_ic_ir / 0.3, 1.0) * 100

    if 0.01 <= cloud_turnover <= 0.35:
        cloud_turnover_sub = 100.0
    elif cloud_turnover > 0.35:
        cloud_turnover_sub = max(0.0, 100.0 - (cloud_turnover - 0.35) / 0.35 * 100)
    else:
        cloud_turnover_sub = 0.0

    cloud_data_sub = min(cloud_data_days / 120, 1.0) * 100
    cloud_alignment_score = (cloud_ic_mean_sub * 0.30 + cloud_ic_ir_sub * 0.30
                             + cloud_turnover_sub * 0.20 + cloud_data_sub * 0.20)

    score = (ic_mean_score * 0.15 + ic_ir_score * 0.15 + stability_score * 0.15
             + ao_score * 0.15 + group_bt_score * 0.15 + cloud_alignment_score * 0.25)
    score = round(_clamp(score, 0, 100), 1)

    grade = "A" if score >= 80 else "B" if score >= 60 else "C" if score >= 40 else "D"

    capped = False
    cap_reason = None
    cagr = report_metrics.get("cagr", 0.0)
    sharpe = report_metrics.get("sharpe", 0.0)
    if cagr < 0 or sharpe < 0:
        if grade in ("A", "B"):
            grade = "C"
            score = min(score, 59.9)
            capped = True
            cap_reason = "negative_cagr" if cagr < 0 else "negative_sharpe"

    cloud_predicted_pass = (
        cloud_ic_mean >= 0.015
        and cloud_ic_ir >= 0.15
        and cloud_turnover <= 0.35
        and cloud_data_days >= 120
    )

    return {
        "score": score, "grade": grade,
        "component_scores": {
            "ic_mean": round(ic_mean_score, 1), "ic_ir": round(ic_ir_score, 1),
            "stability": round(stability_score, 1), "anti_overfit": round(ao_score, 1),
            "group_backtest": round(group_bt_score, 1),
            "cloud_alignment": round(cloud_alignment_score, 1),
        },
        "cloud_predicted_pass": cloud_predicted_pass,
        "capped": capped, "cap_reason": cap_reason,
    }


# ---- Prompt building ----

_FACTOR_CATEGORIES = [
    ("Momentum", "rank(ts_delta(close, 20) / ts_shift(close, 20))"),
    ("Reversal", "rank(-1 * ts_delta(close, 5) / ts_shift(close, 5))"),
    ("Volatility", "rank(ts_std(close/ts_shift(close,1)-1, 20))"),
    ("Volume", "rank(volume / ts_mean(volume, 20))"),
    ("Value", "rank((close - ts_min(close, 60)) / (ts_max(close, 60) - ts_min(close, 60) + 1e-8))"),
    ("Correlation", "rank(ts_corr(close, volume, 20))"),
    ("MeanReversion", "rank((close - ts_mean(close, 20)) / (ts_std(close, 20) + 1e-8))"),
    ("Intraday", "rank((close - open) / (high - low + 1e-8))"),
    ("NonlinearMomentum", "sign_power(ts_delta(close, 20) / close, 0.5) * rank(volume / adv20)"),
    ("DecayWeighted", "decay_linear(rank(ts_corr(vwap, volume, 10)), 5)"),
    ("Interaction", "rank(ts_corr(close, volume, 20)) * rank(ts_delta(close, 10) / close)"),
    ("Conditional", "rank(where(ts_rank(volume, 20) > 0.7, ts_delta(close, 10) / close, 0))"),
]

_SYSTEM_PROMPT_TEMPLATE = """你是一个量化因子表达式优化专家。

{operators_doc}

## 多样性与非线性原则
1. 只能使用上述 SUPPORTED OPERATORS 中列出的函数
2. 优先使用非线性变换（sign_power, tanh, sigmoid, log）捕捉市场动态
3. 组合不同类别的信号（动量+量价+波动率），而非仅调整单一信号的参数
4. 使用交互项（乘法组合）来增强因子区分度
5. 考虑条件因子（where）来捕捉不同市场状态
6. 使用衰减加权（decay_linear）来对近期数据赋予更高权重

## 输出格式要求（必须严格遵守）
只返回一个因子表达式，不要任何解释、分析或推理过程。
不要使用 markdown 代码块、反引号或引号包裹。
你的回复必须是恰好一行可执行的因子表达式。

## 复杂度限制
- 函数嵌套层数不能超过 10 层
- 表达式总长度不能超过 500 个字符
"""


def _build_explore_prompt(
    expression: str, score: float, metrics: dict,
    previous_expressions: list[str], iteration_index: int,
    task_id: str, direction: str | None,
) -> str:
    """Build user prompt for EXPLORE strategy (try completely different approach)."""
    # Select rotated category examples
    seed_str = f"{task_id}:{iteration_index}"
    h = int(hashlib.md5(seed_str.encode()).hexdigest(), 16)
    indices = [(h >> (i * 3)) % 1000 for i in range(len(_FACTOR_CATEGORIES))]
    ranked = sorted(range(len(_FACTOR_CATEGORIES)), key=lambda i: indices[i])
    selected = [_FACTOR_CATEGORIES[i] for i in ranked[:5]]

    parts = [
        f"当前因子: {expression}",
        f"评分: {score}/100 — 需要完全不同的方向",
        "",
        "## 参考因子类别（请选择一个全新方向）",
    ]
    for name, example in selected:
        parts.append(f"- {name}: {example}")

    if previous_expressions:
        parts.append("")
        parts.append("## 禁止重复（以下表达式已使用）")
        for expr in previous_expressions[-10:]:
            parts.append(f"- {expr}")

    if direction:
        parts.append(f"\n## 用户指定方向\n请重点朝以下方向改进：{direction}")

    parts.append("\n请生成一个全新方向的因子表达式：")
    return "\n".join(parts)


# ---- Duplicate detection ----

def _normalize_expression(expr: str) -> str:
    return re.sub(r"\s+", "", expr.lower())


def is_duplicate_expression(expr: str, existing: list[str]) -> bool:
    norm = _normalize_expression(expr)
    return any(_normalize_expression(e) == norm for e in existing)


# ---- Single candidate evaluation ----

def _evaluate_candidate(
    expression: str, params: dict, market_df: pd.DataFrame, user_id: str,
) -> dict:
    """Run backtest + anti-overfit + report + score for a single expression."""
    n_groups = params.get("n_groups", 5)
    holding_period = params.get("holding_period", 5)
    executor = get_executor()
    future = executor.submit_cpu_work(
        _run_backtest_in_process, market_df, expression, n_groups, holding_period,
    )
    result = future.result(timeout=300)

    # Fast anti-overfit (IC stability + half-life only)
    anti_overfit_result = None
    factor_df = result.get("_factor_df")
    if factor_df is not None and len(factor_df) > 100:
        try:
            from .anti_overfit import AntiOverfitDetector
            detector = AntiOverfitDetector(factor_df, holding_period)
            t1 = detector.test_ic_stability()
            t4 = detector.test_half_life()
            fast_passed = sum(1 for t in [t1, t4] if t.passed)
            anti_overfit_result = {
                "score": fast_passed / 2 * 100,
                "recommendation": "推荐" if fast_passed == 2 else "谨慎" if fast_passed == 1 else "需改进",
                "tests": [{"name": t.name, "passed": t.passed, "details": t.details} for t in [t1, t4]],
            }
        except Exception as e:
            logger.warning(f"Anti-overfit failed: {e}")

    # Generate report
    from .market_data import fetch_benchmark_returns
    bm_returns = None
    try:
        bm_returns = fetch_benchmark_returns(
            params.get("benchmark", "hs300"),
            params.get("start_date", "2023-01-01"),
            params.get("end_date", "2025-12-31"),
        )
    except Exception:
        pass

    user_report_dir = Path(__file__).resolve().parent.parent / "reports" / user_id
    user_report_dir.mkdir(parents=True, exist_ok=True)
    report_result = generate_report(
        result["strategy_returns"], benchmark_returns=bm_returns,
        title="Factor Top-Group Backtest", output_dir=str(user_report_dir),
    )
    report_filename = Path(report_result["report_path"]).name

    # Score
    ao_val = anti_overfit_result.get("score") if anti_overfit_result else None
    backtest_summary = {
        "long_short_sharpe": result["long_short_sharpe"],
        "monotonicity_score": result["monotonicity_score"],
        "spread": result["spread"],
        "ic_mean": result.get("ic_mean", 0),
        "rank_ic_mean": result.get("rank_ic_mean", 0),
        "ic_ir": result.get("ic_ir", 0),
        "ic_win_rate": result.get("ic_win_rate", 0),
        "long_short_annual": result.get("long_short_annual", 0),
        "top_group_sharpe": result.get("top_group_sharpe", 0),
        "group_returns": result["group_returns"],
        "turnover": result.get("turnover", 0),
        "wq_fitness": result.get("wq_fitness", 0),
    }
    scoring = compute_factor_score(backtest_summary, report_result["metrics"], ao_val)

    cloud_validation = None
    if scoring["grade"] == "A" and factor_df is not None:
        try:
            from .cloud_client import auto_upload_to_cloud
            cloud_validation = auto_upload_to_cloud(
                expression=expression,
                universe=params.get("universe", "hs300"),
                factor_df=factor_df,
                claimed_ic_mean=result.get("ic_mean"),
                claimed_ic_ir=result.get("ic_ir"),
            )
        except Exception as e:
            logger.warning(f"Cloud auto-upload failed for iteration candidate: {e}")

    return {
        "expression": expression,
        "status": "success",
        "score": scoring["score"],
        "grade": scoring["grade"],
        "component_scores": scoring["component_scores"],
        "backtest_summary": backtest_summary,
        "wq_brain": result.get("wq_brain", {}),
        "anti_overfit": anti_overfit_result,
        "cloud_validation": cloud_validation,
        "report_metrics": report_result["metrics"],
        "report_url": f"/api/v1/reports/{report_filename}",
        "report_filename": report_filename,
        "metrics": {"backtest_summary": backtest_summary, "report_metrics": report_result["metrics"]},
    }


# ---- Main adaptive iteration loop ----

def _call_llm(system_prompt: str, user_prompt: str, temperature: float = 0.9) -> str:
    """Call LLM and return cleaned expression string."""
    import time as _time

    from openai import OpenAI

    from .llm_service import clean_expression as _clean_expression
    from .llm_service import get_llm_api_key, get_llm_base_url, get_llm_model

    api_key = get_llm_api_key()
    if not api_key:
        raise RuntimeError("LLM_API_KEY not set")
    model = get_llm_model()
    client = OpenAI(api_key=api_key, base_url=get_llm_base_url())

    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                max_tokens=256,
                timeout=60,
            )
            return _clean_expression(resp.choices[0].message.content)
        except Exception as e:
            logger.warning(f"LLM call attempt {attempt+1} failed: {e}")
            _time.sleep(3 * (attempt + 1))
    raise RuntimeError("LLM call failed after 3 attempts")


def _validate_expression(expr: str) -> str | None:
    """Validate expression syntax. Returns error string or None if valid."""
    from .llm_service import validate_parentheses as _validate_parentheses
    paren_err = _validate_parentheses(expr)
    if paren_err:
        return f"括号错误: {paren_err}"
    try:
        from .fundamental_data import ALL_FUNDAMENTAL_NAMES as _FN
        dummy = pd.DataFrame({
            "open": [1.0, 2.0, 3.0], "high": [1.1, 2.1, 3.1],
            "low": [0.9, 1.9, 2.9], "close": [1.0, 2.0, 3.0],
            "volume": [100, 200, 300], "amount": [100, 400, 900],
            "pct_change": [0, 100, 50],
            "trade_date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
            **{name: [1.0, 1.1, 1.2] for name in _FN},
        })
        func = parse_expression(expr)
        func(dummy)
        return None
    except Exception as e:
        return f"表达式验证失败: {e}"


def generate_iteration_candidates(
    parent_expression: str,
    parent_metrics: dict,
    parent_score: float,
    parent_grade: str,
    params: dict,
    market_df: pd.DataFrame,
    user_id: str,
    n_candidates: int = 5,
    max_concurrent: int = 50,
    on_progress: Callable[[int, dict], None] | None = None,
    task_id: str = "",
    direction: str | None = None,
) -> list[dict]:
    """Generate N candidate factor improvements using adaptive evolution.

    Serial-adaptive loop: generate → evaluate → analyze trajectory → select strategy → repeat.
    Each candidate builds on the trajectory of all previous candidates.
    """
    from .llm_service import OPERATORS_DOC as _FACTOR_OPERATORS

    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(operators_doc=_FACTOR_OPERATORS)
    all_expressions = [parent_expression]
    trajectory: list[dict] = [{
        "expression": parent_expression,
        "score": parent_score,
        "metrics": parent_metrics,
        "strategy": "parent",
    }]
    candidates: list[dict] = []

    for i in range(n_candidates):
        try:
            # Phase 1: Analyze trajectory
            traj_metrics = analyze_trajectory(trajectory)

            # Phase 2: Select strategy
            current_score = trajectory[-1]["score"] if trajectory else parent_score
            nesting = sum(1 for c in parent_expression if c == '(')
            strategy = select_strategy(traj_metrics, current_score, nesting)
            logger.info(f"[{task_id}] candidate {i}: strategy={strategy.value}, "
                        f"traj_score={current_score}, best={traj_metrics.best_score}")

            # Phase 3: Build prompt based on strategy
            if strategy == EvolutionStrategy.RECOMBINE:
                segments = extract_top_segments(trajectory)
                if len(segments) >= 2:
                    _, user_prompt = build_crossover_prompt(
                        segments, parent_expression, current_score, _FACTOR_OPERATORS)
                else:
                    strategy = EvolutionStrategy.EXPLORE

            if strategy == EvolutionStrategy.EXPLORE:
                user_prompt = _build_explore_prompt(
                    parent_expression, current_score, parent_metrics,
                    all_expressions, i, task_id, direction)

            elif strategy in (EvolutionStrategy.EXPLOIT, EvolutionStrategy.SIMPLIFY):
                # Use best expression as base for mutation
                base_expr = traj_metrics.best_expression or parent_expression
                base_metrics = parent_metrics
                for t in trajectory:
                    if t["expression"] == base_expr:
                        base_metrics = t.get("metrics", parent_metrics)
                        break
                engine = MutationEngine(base_expr, base_metrics, traj_metrics.best_score)
                _, user_prompt = engine.build_mutation_prompt(_FACTOR_OPERATORS)

                # Append anti-repeat and direction
                extra = []
                if all_expressions:
                    extra.append("\n## 禁止重复")
                    for expr in all_expressions[-10:]:
                        extra.append(f"- {expr}")
                if direction:
                    extra.append(f"\n## 用户指定方向\n请重点朝以下方向改进：{direction}")
                user_prompt += "\n".join(extra)

            # Generate expression via LLM (with dedup retries)
            temp = 0.9 if strategy != EvolutionStrategy.EXPLORE else 1.2
            raw_expression = None
            for dedup_attempt in range(4):
                expr = _call_llm(system_prompt, user_prompt, temperature=min(temp + dedup_attempt * 0.2, 1.8))
                err = _validate_expression(expr)
                if err:
                    logger.warning(f"[{task_id}] candidate {i} validation failed: {err}")
                    raw_expression = expr
                    break
                if not is_duplicate_expression(expr, all_expressions):
                    raw_expression = expr
                    break
                logger.info(f"[{task_id}] candidate {i} duplicate, retry {dedup_attempt+1}")
            if raw_expression is None:
                raw_expression = expr  # last attempt even if duplicate

            # Validate
            err = _validate_expression(raw_expression)
            if err:
                result = {"expression": raw_expression, "status": "failed", "error": err, "score": 0}
                candidates.append(result)
                trajectory.append({"expression": raw_expression, "score": 0, "strategy": strategy.value})
                if on_progress:
                    on_progress(len(candidates), result)
                continue

            all_expressions.append(raw_expression)

            # Evaluate
            result = _evaluate_candidate(raw_expression, params, market_df, user_id)
            result["strategy_used"] = strategy.value
            candidates.append(result)

            # Record in trajectory
            trajectory.append({
                "expression": raw_expression,
                "score": result.get("score", 0),
                "metrics": result.get("metrics", {}),
                "strategy": strategy.value,
            })

            if on_progress:
                on_progress(len(candidates), result)

        except Exception as e:
            logger.error(f"[{task_id}] candidate {i} failed: {traceback.format_exc()}")
            result = {"expression": "unknown", "status": "failed", "error": str(e), "score": 0}
            candidates.append(result)
            trajectory.append({"expression": "unknown", "score": 0, "strategy": "error"})
            if on_progress:
                on_progress(len(candidates), result)

    candidates.sort(key=lambda c: (c.get("status") == "success", c.get("score", 0)), reverse=True)
    return candidates


# Legacy alias
build_iterate_prompt = None  # Removed — prompts now built inside generate_iteration_candidates
