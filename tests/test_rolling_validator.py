"""Tests for rolling_validator.py — walk-forward validation."""

import numpy as np
import pandas as pd
import pytest

from quantgpt.rolling_validator import RollingResult, RollingValidator, WindowResult, run_rolling_validation


@pytest.fixture
def factor_df():
    """Synthetic factor data spanning 6 years for rolling validation."""
    dates = pd.bdate_range("2018-01-02", periods=1500)
    stocks = [f"00000{i}.SZ" for i in range(1, 11)]
    rng = np.random.RandomState(42)
    rows = []
    for d in dates:
        for s in stocks:
            rows.append({
                "trade_date": d,
                "stock_code": s,
                "factor_value": rng.randn(),
                "daily_ret": rng.randn() * 0.02,
            })
    return pd.DataFrame(rows)


@pytest.fixture
def short_df():
    """Too-short data for rolling validation."""
    dates = pd.bdate_range("2024-01-02", periods=50)
    stocks = ["000001.SZ", "000002.SZ"]
    rng = np.random.RandomState(42)
    rows = []
    for d in dates:
        for s in stocks:
            rows.append({
                "trade_date": d,
                "stock_code": s,
                "factor_value": rng.randn(),
                "daily_ret": rng.randn() * 0.02,
            })
    return pd.DataFrame(rows)


class TestRollingValidator:
    def test_returns_rolling_result(self, factor_df):
        validator = RollingValidator(factor_df, holding_period=5)
        result = validator.run()
        assert isinstance(result, RollingResult)

    def test_score_in_range(self, factor_df):
        validator = RollingValidator(factor_df, holding_period=5)
        result = validator.run()
        assert 0 <= result.score <= 100

    def test_windows_generated(self, factor_df):
        validator = RollingValidator(factor_df, holding_period=5)
        result = validator.run()
        assert len(result.windows) > 0

    def test_window_results_have_correct_fields(self, factor_df):
        validator = RollingValidator(factor_df, holding_period=5)
        result = validator.run()
        w = result.windows[0]
        assert isinstance(w, WindowResult)
        assert w.window_index == 0
        assert isinstance(w.train_ic, float)
        assert isinstance(w.test_ic, float)

    def test_summary_contains_keys(self, factor_df):
        validator = RollingValidator(factor_df, holding_period=5)
        result = validator.run()
        assert "n_windows" in result.summary
        assert "mean_test_ic" in result.summary
        assert "mean_test_ir" in result.summary

    def test_decay_analysis(self, factor_df):
        validator = RollingValidator(factor_df, holding_period=5)
        result = validator.run()
        assert "status" in result.decay_analysis

    def test_short_data_returns_zero_score(self, short_df):
        validator = RollingValidator(short_df, holding_period=5)
        result = validator.run()
        assert result.score == 0
        assert "error" in result.summary

    def test_custom_window_params(self, factor_df):
        validator = RollingValidator(
            factor_df,
            holding_period=10,
            train_years=2,
            valid_years=1,
            test_years=1,
            step_months=6,
        )
        result = validator.run()
        assert isinstance(result, RollingResult)

    def test_with_anti_overfit(self, factor_df):
        validator = RollingValidator(factor_df, holding_period=5)
        result = validator.run(run_anti_overfit=True)
        ao_scores = [w.anti_overfit_score for w in result.windows if w.anti_overfit_score is not None]
        assert len(ao_scores) > 0


class TestRunRollingValidation:
    def test_convenience_function_returns_dict(self, factor_df):
        result = run_rolling_validation(factor_df, holding_period=5)
        assert isinstance(result, dict)
        assert "score" in result
        assert "summary" in result
        assert "windows" in result
        assert "decay_analysis" in result

    def test_windows_serializable(self, factor_df):
        result = run_rolling_validation(factor_df, holding_period=5)
        for w in result["windows"]:
            assert "train_period" in w
            assert "test_ic" in w
            assert isinstance(w["train_ic"], float)


class TestCalcIcIr:
    def test_empty_df_returns_zeros(self):
        validator = RollingValidator(
            pd.DataFrame(columns=["trade_date", "stock_code", "factor_value", "daily_ret"]),
            holding_period=5,
        )
        ic, ir = validator._calc_ic_ir(pd.DataFrame())
        assert ic == 0.0
        assert ir == 0.0

    def test_constant_factor_returns_zero_ic(self, factor_df):
        df = factor_df.copy()
        df["factor_value"] = 1.0
        validator = RollingValidator(df, holding_period=5)
        subset = df[df["trade_date"] < df["trade_date"].quantile(0.3)]
        ic, ir = validator._calc_ic_ir(subset)
        assert ic == 0.0


class TestCompositeScore:
    def test_empty_windows_returns_zero(self):
        validator = RollingValidator(
            pd.DataFrame(columns=["trade_date", "stock_code", "factor_value", "daily_ret"]),
        )
        assert validator._compute_composite_score([]) == 0.0

    def test_perfect_windows_give_high_score(self):
        validator = RollingValidator(
            pd.DataFrame(columns=["trade_date", "stock_code", "factor_value", "daily_ret"]),
        )
        windows = [
            WindowResult(
                window_index=i,
                train_start="2020-01-01", train_end="2022-01-01",
                valid_start="2022-01-01", valid_end="2023-01-01",
                test_start="2023-01-01", test_end="2024-01-01",
                train_ic=0.08, train_ir=1.5,
                valid_ic=0.07, valid_ir=1.3,
                test_ic=0.06, test_ir=1.2,
                anti_overfit_score=90,
                sharpe=2.0,
            )
            for i in range(3)
        ]
        score = validator._compute_composite_score(windows)
        assert score > 60

    def test_single_window_gets_stability_default(self):
        validator = RollingValidator(
            pd.DataFrame(columns=["trade_date", "stock_code", "factor_value", "daily_ret"]),
        )
        windows = [
            WindowResult(
                window_index=0,
                train_start="2020-01-01", train_end="2022-01-01",
                valid_start="2022-01-01", valid_end="2023-01-01",
                test_start="2023-01-01", test_end="2024-01-01",
                train_ic=0.05, train_ir=1.0,
                valid_ic=0.04, valid_ir=0.8,
                test_ic=0.03, test_ir=0.6,
            )
        ]
        score = validator._compute_composite_score(windows)
        assert 0 < score < 100
