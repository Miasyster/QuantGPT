"""Tests for adversarial_validator module."""

import numpy as np
import pandas as pd

from quantgpt.adversarial_validator import (
    AdversarialValidator,
    run_adversarial_validation,
)


def _make_factor_df(n_stocks=50, n_days=200, signal_strength=0.05, seed=42):
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range("2023-01-01", periods=n_days)
    stocks = [f"sh.{600000 + i:06d}" for i in range(n_stocks)]

    rows = []
    for d in dates:
        for s in stocks:
            fv = rng.randn()
            ret = signal_strength * fv + rng.randn() * 0.02
            rows.append({"trade_date": d, "stock_code": s, "factor_value": fv, "daily_ret": ret})
    return pd.DataFrame(rows)


def _make_noise_df(n_stocks=50, n_days=200, seed=42):
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range("2023-01-01", periods=n_days)
    stocks = [f"sh.{600000 + i:06d}" for i in range(n_stocks)]

    rows = []
    for d in dates:
        for s in stocks:
            rows.append({
                "trade_date": d, "stock_code": s,
                "factor_value": rng.randn(),
                "daily_ret": rng.randn() * 0.02,
            })
    return pd.DataFrame(rows)


class TestAdversarialValidator:
    def test_run_all_returns_required_keys(self):
        df = _make_factor_df()
        result = run_adversarial_validation(df, holding_period=5)
        assert "score" in result
        assert "recommendation" in result
        assert "passed_count" in result
        assert "total_count" in result
        assert result["total_count"] == 4
        assert len(result["tests"]) == 4

    def test_strong_factor_passes_most(self):
        df = _make_factor_df(signal_strength=0.08)
        result = run_adversarial_validation(df, holding_period=5)
        assert result["passed_count"] >= 2
        assert result["score"] >= 50

    def test_pure_noise_fails(self):
        df = _make_noise_df()
        result = run_adversarial_validation(df, holding_period=5)
        assert result["passed_count"] <= 2

    def test_label_permutation(self):
        df = _make_factor_df(signal_strength=0.08)
        v = AdversarialValidator(df, holding_period=5)
        t = v.test_label_permutation(n_perms=30)
        assert t.name == "标签置换检验"
        assert "real_ic" in t.details
        assert "perm_95th_abs" in t.details

    def test_temporal_shuffle(self):
        df = _make_factor_df(signal_strength=0.08)
        v = AdversarialValidator(df, holding_period=5)
        t = v.test_temporal_shuffle(block_size=20)
        assert t.name == "时序打乱检验"
        assert "ratio" in t.details

    def test_random_universe(self):
        df = _make_factor_df(signal_strength=0.08)
        v = AdversarialValidator(df, holding_period=5)
        t = v.test_random_universe(n_trials=20)
        assert t.name == "随机股票池检验"
        assert "consistency" in t.details

    def test_noise_injection(self):
        df = _make_factor_df(signal_strength=0.08)
        v = AdversarialValidator(df, holding_period=5)
        t = v.test_noise_injection()
        assert t.name == "噪声注入检验"
        assert "retention_at_0.5x" in t.details

    def test_insufficient_data(self):
        df = _make_factor_df(n_stocks=3, n_days=10)
        result = run_adversarial_validation(df, holding_period=5)
        for t in result["tests"]:
            assert "error" in t["details"] or isinstance(t["passed"], bool)

    def test_score_range(self):
        df = _make_factor_df()
        result = run_adversarial_validation(df, holding_period=5)
        assert 0 <= result["score"] <= 100

    def test_recommendation_values(self):
        df = _make_factor_df()
        result = run_adversarial_validation(df, holding_period=5)
        assert result["recommendation"] in ("通过", "基本通过", "存疑", "高风险")
