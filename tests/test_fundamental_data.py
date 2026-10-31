"""Tests for quantgpt.fundamental_data — variable registry, detection, and quarter logic."""

import numpy as np
import pandas as pd
import pytest

from quantgpt.fundamental_data import (
    ALL_FUNDAMENTAL_NAMES,
    DERIVED_VARIABLES,
    FUNDAMENTAL_VARIABLES,
    FundamentalDataFetcher,
    _quarter_range,
    detect_fundamental_vars,
    enrich_market_data,
    get_needed_apis,
)


# ─── Variable registry consistency ───────────────────────────────

class TestVariableRegistry:
    def test_fundamental_variables_not_empty(self):
        assert len(FUNDAMENTAL_VARIABLES) > 15

    def test_derived_variables_not_empty(self):
        assert len(DERIVED_VARIABLES) >= 5

    def test_all_names_is_superset(self):
        for name in FUNDAMENTAL_VARIABLES:
            assert name in ALL_FUNDAMENTAL_NAMES
        for name in DERIVED_VARIABLES:
            assert name in ALL_FUNDAMENTAL_NAMES
        assert "dividend_yield" in ALL_FUNDAMENTAL_NAMES

    def test_derived_deps_exist(self):
        for derived, deps in DERIVED_VARIABLES.items():
            for dep in deps:
                assert dep in FUNDAMENTAL_VARIABLES, f"{derived} depends on {dep} which is not in FUNDAMENTAL_VARIABLES"

    def test_no_overlap_fundamental_derived(self):
        overlap = set(FUNDAMENTAL_VARIABLES.keys()) & set(DERIVED_VARIABLES.keys())
        assert len(overlap) == 0, f"Variables appear in both registries: {overlap}"

    def test_api_names_valid(self):
        valid_apis = {"profit", "growth", "balance", "operation", "dupont", "cash_flow"}
        for name, (api, _) in FUNDAMENTAL_VARIABLES.items():
            assert api in valid_apis, f"{name} has invalid api: {api}"


# ─── detect_fundamental_vars ─────────────────────────────────────

class TestDetectFundamentalVars:
    def test_simple_expression(self):
        result = detect_fundamental_vars("rank(roe)")
        assert result == {"roe"}

    def test_multiple_vars(self):
        result = detect_fundamental_vars("rank(roe) + zscore(pe)")
        assert result == {"roe", "pe"}

    def test_no_fundamental_vars(self):
        result = detect_fundamental_vars("rank(close / ts_mean(close, 20))")
        assert result == set()

    def test_case_insensitive(self):
        result = detect_fundamental_vars("rank(ROE)")
        assert result == {"roe"}

    def test_derived_detected(self):
        result = detect_fundamental_vars("zscore(pb)")
        assert result == {"pb"}

    def test_mixed_fundamental_and_market(self):
        result = detect_fundamental_vars("rank(close * roe / pe)")
        assert result == {"roe", "pe"}

    def test_dividend_yield(self):
        result = detect_fundamental_vars("rank(dividend_yield)")
        assert result == {"dividend_yield"}

    def test_partial_match_excluded(self):
        result = detect_fundamental_vars("rank(roe_something)")
        assert "roe" not in result


# ─── get_needed_apis ─────────────────────────────────────────────

class TestGetNeededApis:
    def test_single_direct_var(self):
        apis = get_needed_apis({"roe"})
        assert apis == {"profit"}

    def test_multiple_apis(self):
        apis = get_needed_apis({"roe", "current_ratio"})
        assert "profit" in apis
        assert "balance" in apis

    def test_derived_var_expands(self):
        apis = get_needed_apis({"pe"})
        assert "profit" in apis

    def test_pb_needs_profit(self):
        apis = get_needed_apis({"pb"})
        assert "profit" in apis

    def test_empty_input(self):
        assert get_needed_apis(set()) == set()

    def test_growth_vars(self):
        apis = get_needed_apis({"yoy_ni"})
        assert apis == {"growth"}

    def test_dupont_vars(self):
        apis = get_needed_apis({"dupont_roe"})
        assert apis == {"dupont"}

    def test_operation_vars(self):
        apis = get_needed_apis({"asset_turnover"})
        assert apis == {"operation"}

    def test_cash_flow_vars(self):
        apis = get_needed_apis({"cfo_to_np"})
        assert apis == {"cash_flow"}


# ─── _quarter_range ──────────────────────────────────────────────

class TestQuarterRange:
    def test_single_year(self):
        quarters = _quarter_range("2024-01-01", "2024-12-31")
        assert (2023, 1) in quarters
        assert (2024, 4) in quarters
        assert len(quarters) == 8

    def test_lookback_one_year(self):
        quarters = _quarter_range("2024-06-01", "2024-06-30")
        assert (2023, 1) in quarters

    def test_cross_year(self):
        quarters = _quarter_range("2023-10-01", "2024-03-31")
        years = {y for y, q in quarters}
        assert 2022 in years
        assert 2023 in years
        assert 2024 in years

    def test_order(self):
        quarters = _quarter_range("2024-01-01", "2024-06-30")
        for i in range(len(quarters) - 1):
            y1, q1 = quarters[i]
            y2, q2 = quarters[i + 1]
            assert (y1, q1) < (y2, q2)


# ─── enrich_market_data edge cases ───────────────────────────────

class TestEnrichMarketData:
    def test_empty_vars_returns_unchanged(self):
        df = pd.DataFrame({
            "trade_date": pd.to_datetime(["2024-01-02"]),
            "stock_code": ["sh.600519"],
            "close": [1800.0],
        })
        result = enrich_market_data(df, set(), ["sh.600519"], "2024-01-01", "2024-12-31")
        assert result is df

    def test_missing_column_graceful(self):
        df = pd.DataFrame({
            "trade_date": pd.to_datetime(["2024-01-02"]),
            "stock_code": ["sh.600519"],
            "close": [1800.0],
        })
        result = enrich_market_data(df, {"roe"}, ["sh.600519"], "2024-01-01", "2024-01-31")
        assert result is not None
        assert len(result) >= 0

    def test_align_to_daily_handles_mixed_datetime_precision(self):
        market = pd.DataFrame({
            "trade_date": pd.to_datetime(["2024-01-02", "2024-05-06"]).astype("datetime64[ns]"),
            "stock_code": ["sh.600519", "sh.600519"],
            "close": [1800.0, 1700.0],
        })
        quarterly = pd.DataFrame({
            "stock_code": ["sh.600519"],
            "pub_date": pd.to_datetime(["2024-04-30"]).astype("datetime64[us]"),
            "roe": [0.1],
        })
        result = FundamentalDataFetcher().align_to_daily(quarterly, market, {"roe"})
        assert result["roe"].isna().tolist() == [True, False]
