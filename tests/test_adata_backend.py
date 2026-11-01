"""Tests for the optional adata market data backend."""

import sys
from types import SimpleNamespace

import pandas as pd
import pytest

from quantgpt import adata_backend, market_data


class FakeClient:
    instances = []

    def __init__(self, data_dir, provider_priority):
        self.data_dir = data_dir
        self.provider_priority = provider_priority
        FakeClient.instances.append(self)

    def query_daily(self, codes, start_date, end_date):
        return pd.DataFrame({
            "trade_date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
            "stock_code": ["sh.600519", "sh.600519"],
            "close": [10.0, 11.0],
            "volume": [100.0, 0.0],
            "amount": [1000.0, 0.0],
        })

    def query_benchmark(self, name, start_date, end_date):
        return pd.DataFrame({
            "trade_date": pd.to_datetime(["2024-01-03", "2024-01-02"]),
            "close": [101.0, 100.0],
            "daily_return": [0.01, None],
        })


@pytest.fixture
def enabled(monkeypatch, tmp_path):
    FakeClient.instances = []
    monkeypatch.setitem(sys.modules, "adata", SimpleNamespace(DataClient=FakeClient))
    monkeypatch.setattr(adata_backend, "_client", None)
    monkeypatch.setenv("QUANTGPT_DATA_BACKEND", "adata")
    monkeypatch.setenv("ADATA_DIR", str(tmp_path))
    monkeypatch.setattr(market_data, "CACHE_ONLY", False)


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("QUANTGPT_DATA_BACKEND", raising=False)
    assert adata_backend.is_enabled() is False


def test_never_requests_rqdatac(enabled):
    market_data.MarketDataFetcher().fetch_stocks(["sh.600519"], "2024-01-01", "2024-01-31")
    assert FakeClient.instances[0].provider_priority == ["baostock", "akshare"]


def test_fetch_stocks_adds_vwap(enabled):
    df = market_data.MarketDataFetcher().fetch_stocks(["sh.600519"], "2024-01-01", "2024-01-31")
    assert df["vwap"].iloc[0] == 10.0
    assert pd.isna(df["vwap"].iloc[1])


def test_benchmark_returns_match_legacy_shape(enabled):
    ret = market_data.fetch_benchmark_returns("hs300", "2024-01-01", "2024-01-31")
    assert ret.name == market_data.BENCHMARK_CODES["hs300"]["name"]
    assert ret.index.tolist() == [pd.Timestamp("2024-01-03")]
    assert ret.tolist() == [0.01]


def test_cache_only_mode_does_not_use_adata(enabled, monkeypatch, tmp_path):
    monkeypatch.setattr(market_data, "CACHE_ONLY", True)
    market_data.MarketDataFetcher(cache_dir=str(tmp_path)).fetch_stocks(["sh.600519"], "2024-01-01", "2024-01-31")
    assert FakeClient.instances == []
