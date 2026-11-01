"""Optional market data backend powered by adata.

Enable with QUANTGPT_DATA_BACKEND=adata (requires: pip install 'quantgpt[adata]').
Only daily bars and benchmark returns go through adata; universes, fundamentals
and industry data stay in their existing modules.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd

# Explicit on purpose: adata's default priority includes rqdatac, which must
# never be called automatically. An empty list would also fall back to it.
_PROVIDER_PRIORITY = ["baostock", "akshare"]

_client = None
_client_dir = None


def is_enabled() -> bool:
    return os.environ.get("QUANTGPT_DATA_BACKEND", "").lower() == "adata"


def _data_dir() -> str:
    return os.environ.get("ADATA_DIR") or str(Path(__file__).resolve().parent.parent / "data" / "adata")


def _get_client():
    global _client, _client_dir
    data_dir = _data_dir()
    if _client is None or _client_dir != data_dir:
        from adata import DataClient

        _client = DataClient(data_dir=data_dir, provider_priority=list(_PROVIDER_PRIORITY))
        _client_dir = data_dir
    return _client


def fetch_stocks(stock_codes: list[str], start_date: str, end_date: str) -> pd.DataFrame | None:
    df = _get_client().query_daily(stock_codes, start_date, end_date)
    if df is None or df.empty:
        return None
    result = df.reset_index(drop=True)
    result["vwap"] = result["amount"] / result["volume"].replace(0, np.nan)
    return result


def fetch_benchmark_returns(benchmark: str, name: str, start_date: str, end_date: str | None) -> pd.Series | None:
    df = _get_client().query_benchmark(benchmark, start_date, end_date)
    if df is None or df.empty:
        return None
    ret = df.sort_values("trade_date").set_index("trade_date")["daily_return"].dropna()
    ret.name = name
    return ret
