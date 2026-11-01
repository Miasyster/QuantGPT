"""Market data fetcher — QuantGPT
Copyright (c) 2026 Miasyster. Licensed under the MIT License.
https://github.com/Miasyster/QuantGPT

baostock + akshare (free) + Parquet caching. rqdatac optional.
"""

import logging
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from . import adata_backend

logger = logging.getLogger(__name__)

# Global lock for baostock — it only supports one session per process
_bs_lock = threading.Lock()

try:
    import baostock as bs
    HAS_BAOSTOCK = True
except ImportError:
    HAS_BAOSTOCK = False

try:
    import rqdatac
    HAS_RQDATAC = True
except ImportError:
    HAS_RQDATAC = False

# rqdatac lazy initialization
_rq_lock = threading.Lock()
_rq_initialized = False
_rq_disabled = True

# Project root for default paths
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Cache-only mode: when set, never fetch from remote, only use cached data
CACHE_ONLY = os.environ.get("QUANTGPT_CACHE_ONLY", "").lower() in ("1", "true", "yes")

BENCHMARK_CODES = {
    "hs300":  {"baostock": "sh.000300", "rqdatac": "000300.XSHG", "name": "沪深300"},
    "zz500":  {"baostock": "sh.000905", "rqdatac": "000905.XSHG", "name": "中证500"},
    "csi500": {"baostock": "sh.000905", "rqdatac": "000905.XSHG", "name": "中证500"},  # alias
    "csi1000": {"baostock": "sh.000852", "rqdatac": "000852.XSHG", "name": "中证1000"},
    "sz50":   {"baostock": "sh.000016", "rqdatac": "000016.XSHG", "name": "上证50"},
}

# rqdatac index codes for universe fetching
_RQ_INDEX_CODES = {
    "hs300": "000300.XSHG",
    "csi500": "000905.XSHG",
    "zz500": "000905.XSHG",
    "csi1000": "000852.XSHG",
    # csi2000: no direct rqdatac index, derived from exclusion
}

# Pre-defined stock universes
UNIVERSES = {
    "small_scale": [
        "sh.600519", "sh.601318", "sz.000858", "sz.000333", "sh.600036",
    ],
}

# --- PLACEHOLDER_MARKET_DATA ---


# ─── Code conversion helpers ───────────────────────────────────────

def _to_rq_code(bs_code: str) -> str:
    """Convert baostock code to rqdatac code: sh.600519 → 600519.XSHG"""
    prefix, num = bs_code.split(".")
    suffix = "XSHG" if prefix == "sh" else "XSHE"
    return f"{num}.{suffix}"


def _from_rq_code(rq_code: str) -> str:
    """Convert rqdatac code to baostock code: 600519.XSHG → sh.600519"""
    num, suffix = rq_code.split(".")
    prefix = "sh" if suffix == "XSHG" else "sz"
    return f"{prefix}.{num}"


# ─── rqdatac initialization ────────────────────────────────────────

def _rqdatac_init() -> bool:
    """Lazy-init rqdatac session. Returns True if ready to use.

    Blocked by _rq_disabled (True by default). Only unblocked inside
    the ``enable_rqdatac()`` context manager (manual admin triggers).
    """
    global _rq_initialized
    if _rq_disabled:
        return False
    if not HAS_RQDATAC:
        return False
    if _rq_initialized:
        return True

    username = os.environ.get("RQDATAC_USERNAME", "")
    password = os.environ.get("RQDATAC_PASSWORD", "")
    if not username or not password:
        return False

    with _rq_lock:
        if _rq_initialized:
            return True
        try:
            rqdatac.init(username, password)
            _rq_initialized = True
            logger.info("rqdatac initialized successfully")
            return True
        except Exception as e:
            logger.warning(f"rqdatac init failed: {e}")
            return False


from contextlib import contextmanager


@contextmanager
def enable_rqdatac():
    """Temporarily allow rqdatac calls. For manual admin triggers only."""
    global _rq_disabled
    _rq_disabled = False
    try:
        yield
    finally:
        _rq_disabled = True


# ─── baostock helpers (unchanged) ──────────────────────────────────

def _baostock_login():
    """Login to baostock, return True on success. Retries on network errors."""
    if not HAS_BAOSTOCK:
        raise RuntimeError("baostock is not installed")
    for attempt in range(3):
        try:
            lg = bs.login()
            if lg.error_code == "0":
                return True
            if attempt < 2:
                logger.warning(f"baostock login attempt {attempt+1} failed: {lg.error_msg}, retrying...")
                time.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError(f"baostock login failed: {lg.error_msg}")
        except RuntimeError:
            raise
        except Exception as e:
            if attempt < 2:
                logger.warning(f"baostock login attempt {attempt+1} error: {e}, retrying...")
                time.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError(f"baostock login error: {e}")
    return True


def _baostock_logout():
    try:
        bs.logout()
    except Exception:
        pass


# ─── Universe functions ────────────────────────────────────────────

def get_universe(name: str, date: str | None = None) -> list[str]:
    """Return stock code list for a named universe.

    Supports: small_scale (static), hs300, csi500/zz500, csi1000, csi2000.
    Uses rqdatac as primary source, baostock as fallback.
    """
    if name in UNIVERSES:
        return UNIVERSES[name]

    if name in ("hs300", "csi500", "zz500", "csi1000", "csi2000"):
        return _fetch_index_constituents(name, date)

    raise ValueError(f"Unknown universe: {name}. Available: {list(UNIVERSES.keys()) + ['hs300', 'csi500', 'zz500', 'csi1000', 'csi2000']}")


def _fetch_index_constituents(name: str, date: str | None = None) -> list[str]:
    """Fetch index constituents: cache → rqdatac → baostock."""
    date = date or datetime.now().strftime("%Y-%m-%d")

    # Monthly file cache
    cache_dir = _PROJECT_ROOT / "data" / "universe"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{name}_{date[:7]}.txt"

    if cache_path.exists():
        codes = cache_path.read_text().strip().split("\n")
        if len(codes) > 10:
            logger.info(f"{name} loaded from cache: {len(codes)} stocks")
            return codes

    if CACHE_ONLY:
        logger.warning(f"Cache-only mode: {name} constituents not cached for {date[:7]}, returning empty list")
        return []

    # Try rqdatac
    rq_index = _RQ_INDEX_CODES.get(name)
    if rq_index and _rqdatac_init():
        try:
            rq_codes = rqdatac.index_components(rq_index, date=date)
            if rq_codes and len(rq_codes) > 10:
                codes = [_from_rq_code(c) for c in rq_codes]
                cache_path.write_text("\n".join(codes))
                logger.info(f"[rqdatac] Fetched {len(codes)} constituents for {name}")
                return codes
        except Exception as e:
            logger.warning(f"rqdatac index_components({name}) failed: {e}, falling back to baostock")

    # Fallback: baostock (only supports hs300 / csi500)
    if name in ("hs300", "csi500", "zz500"):
        return _fetch_index_constituents_bs(name, date, cache_path)

    # For csi1000/csi2000 without rqdatac, use derivation
    if name == "csi1000":
        return _derive_csi1000(date, cache_path)
    if name == "csi2000":
        return _derive_csi2000(date, cache_path)

    return []


def _fetch_index_constituents_bs(name: str, date: str, cache_path: Path) -> list[str]:
    """Fetch index constituents from baostock."""
    with _bs_lock:
        _baostock_login()
        try:
            if name == "hs300":
                rs = bs.query_hs300_stocks(date)
            else:  # csi500 / zz500
                rs = bs.query_zz500_stocks(date)

            codes = []
            while rs.error_code == "0" and rs.next():
                row = rs.get_row_data()
                codes.append(row[1])  # code column
            logger.info(f"[baostock] Fetched {len(codes)} constituents for {name}")
            if codes:
                cache_path.write_text("\n".join(codes))
            return codes
        finally:
            _baostock_logout()


def _derive_csi1000(date: str, cache_path: Path) -> list[str]:
    """Derive CSI 1000 = all A - HS300 - CSI500 (baostock fallback)."""
    hs300 = set(_fetch_index_constituents("hs300", date))
    csi500 = set(_fetch_index_constituents("csi500", date))
    exclude = hs300 | csi500

    all_stocks = _fetch_all_stock_codes(date)
    remaining = [c for c in all_stocks if c not in exclude]
    result = remaining[:1000]
    logger.info(f"CSI1000 derived: {len(result)} stocks (all_a={len(all_stocks)}, excluded={len(exclude)})")

    if len(result) > 100:
        cache_path.write_text("\n".join(result))
    return result


def _derive_csi2000(date: str, cache_path: Path) -> list[str]:
    """Derive CSI 2000 = all A - HS300 - CSI500 - CSI1000 (baostock fallback)."""
    csi1000 = set(_fetch_index_constituents("csi1000", date))
    hs300 = set(_fetch_index_constituents("hs300", date))
    csi500 = set(_fetch_index_constituents("csi500", date))
    exclude = hs300 | csi500 | csi1000

    all_stocks = _fetch_all_stock_codes(date)
    remaining = [c for c in all_stocks if c not in exclude]
    result = remaining[:2000]
    logger.info(f"CSI2000 derived: {len(result)} stocks (all_a={len(all_stocks)}, excluded={len(exclude)})")

    if len(result) > 100:
        cache_path.write_text("\n".join(result))
    return result


def _fetch_all_stock_codes(date: str | None = None) -> list[str]:
    """Fetch all A-share stock codes: rqdatac → baostock."""
    date = date or datetime.now().strftime("%Y-%m-%d")

    # Monthly cache
    cache_dir = _PROJECT_ROOT / "data" / "universe"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"all_a_{date[:7]}.txt"

    if cache_path.exists():
        codes = cache_path.read_text().strip().split("\n")
        if len(codes) > 100:
            return codes

    if CACHE_ONLY:
        logger.warning("Cache-only mode: all_a stocks not cached, returning empty")
        return []

    # Try rqdatac
    if _rqdatac_init():
        try:
            df = rqdatac.all_instruments(type='CS', date=date)
            if df is not None and len(df) > 100:
                rq_codes = df['order_book_id'].tolist()
                codes = [_from_rq_code(c) for c in rq_codes
                         if c.endswith('.XSHG') or c.endswith('.XSHE')]
                # Exclude index-like codes (sh.000xxx)
                codes = [c for c in codes if not c.startswith("sh.000")]
                if len(codes) > 100:
                    logger.info(f"[rqdatac] All A-share stocks: {len(codes)}")
                    cache_path.write_text("\n".join(codes))
                    return codes
        except Exception as e:
            logger.warning(f"rqdatac all_instruments failed: {e}, falling back to baostock")

    # Fallback: baostock
    with _bs_lock:
        _baostock_login()
        try:
            for offset in range(0, 10):
                try_date = (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=offset)).strftime("%Y-%m-%d")
                rs = bs.query_all_stock(day=try_date)
                codes = []
                while rs.error_code == "0" and rs.next():
                    row = rs.get_row_data()
                    code = row[0]
                    if not (code.startswith("sh.") or code.startswith("sz.")):
                        continue
                    if code.startswith("sh.000"):
                        continue
                    if code.startswith("bj."):
                        continue
                    codes.append(code)
                if len(codes) > 100:
                    logger.info(f"[baostock] All A-share stocks on {try_date}: {len(codes)}")
                    break

            if len(codes) > 100:
                cache_path.write_text("\n".join(codes))
            else:
                logger.warning(f"Failed to get all_a stocks near {date}, got {len(codes)}")
            return codes
        finally:
            _baostock_logout()


# --- PLACEHOLDER_FETCHER ---


def _transform_rq_to_schema(rq_df: pd.DataFrame, bs_code: str) -> pd.DataFrame:
    """Transform a single-stock rqdatac DataFrame to the standard schema.

    Input: rqdatac get_price output (MultiIndex or single-index) for ONE stock.
    Output: DataFrame with columns: trade_date, stock_code, open, high, low, close,
            volume, amount, pct_change.
    """
    df = rq_df.reset_index()

    # Handle MultiIndex (order_book_id, date) or single-index (date)
    if "date" in df.columns:
        df = df.rename(columns={"date": "trade_date"})
    elif "index" in df.columns:
        df = df.rename(columns={"index": "trade_date"})

    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["stock_code"] = bs_code

    # Map rqdatac columns → standard schema
    if "total_turnover" in df.columns:
        df["amount"] = df["total_turnover"]
    elif "amount" not in df.columns:
        df["amount"] = 0.0

    # Compute pct_change from prev_close if available, else from close
    if "prev_close" in df.columns:
        df["pct_change"] = ((df["close"] / df["prev_close"]) - 1) * 100
        df.loc[df["prev_close"].isna() | (df["prev_close"] == 0), "pct_change"] = np.nan
    else:
        df["pct_change"] = df["close"].pct_change() * 100

    for col in ("open", "high", "low", "close", "volume", "amount", "pct_change"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    result = df[["trade_date", "stock_code", "open", "high", "low", "close", "volume", "amount", "pct_change"]].copy()
    return result.sort_values("trade_date")


class MarketDataFetcher:
    """A-share market data fetcher with per-stock Parquet caching."""

    def __init__(self, cache_dir: str | None = None):
        self.cache_dir = cache_dir or str(_PROJECT_ROOT / "data")
        self.stock_cache_dir = os.path.join(self.cache_dir, "stocks")
        os.makedirs(self.stock_cache_dir, exist_ok=True)

    @staticmethod
    def _normalize_stock_code(stock_code: str) -> str:
        """Normalize to baostock format: sh.600519 / sz.000001."""
        stock_code = stock_code.strip()
        if "." in stock_code:
            parts = stock_code.split(".")
            if len(parts) == 2:
                if parts[1].upper() in ("SH", "SZ"):
                    return f"{parts[1].lower()}.{parts[0]}"
                if parts[0].lower() in ("sh", "sz"):
                    return f"{parts[0].lower()}.{parts[1]}"
        if stock_code[:2].lower() in ("sh", "sz"):
            return f"{stock_code[:2].lower()}.{stock_code[2:]}"
        return stock_code

    def _cache_path(self, stock_code: str) -> str:
        safe = self._normalize_stock_code(stock_code).replace(".", "_")
        return os.path.join(self.stock_cache_dir, f"{safe}.parquet")

    def _load_cache(self, stock_code: str) -> pd.DataFrame | None:
        path = self._cache_path(stock_code)
        if os.path.exists(path):
            try:
                df = pd.read_parquet(path)
                df["trade_date"] = pd.to_datetime(df["trade_date"])
                return df
            except Exception as e:
                logger.warning(f"Cache load failed for {stock_code}: {e}")
        return None

    def _save_cache(self, stock_code: str, df: pd.DataFrame):
        if df is None or len(df) == 0:
            return
        df.to_parquet(self._cache_path(stock_code), index=False)

    # --- PLACEHOLDER_FETCH_REMOTE ---

    def _fetch_remote_rq(self, stock_codes: list[str], start_date: str, end_date: str) -> dict[str, pd.DataFrame]:
        """Batch fetch stocks from rqdatac. Returns {bs_code: DataFrame} dict."""
        if not _rqdatac_init():
            return {}

        rq_codes = [_to_rq_code(c) for c in stock_codes]
        try:
            rq_df = rqdatac.get_price(
                rq_codes,
                start_date=start_date,
                end_date=end_date,
                frequency='1d',
                adjust_type='pre',
                expect_df=True,
            )
            if rq_df is None or len(rq_df) == 0:
                return {}

            results = {}
            for rq_code, group in rq_df.groupby(level=0):
                bs_code = _from_rq_code(rq_code)
                results[bs_code] = _transform_rq_to_schema(group, bs_code)
            logger.info(f"[rqdatac] Batch fetched {len(results)} stocks")
            return results
        except Exception as e:
            logger.warning(f"rqdatac batch fetch failed: {e}")
            return {}

    def _fetch_remote_bs(self, stock_code: str, start_date: str, end_date: str, already_logged_in: bool = False) -> pd.DataFrame | None:
        """Fetch single stock daily data from baostock."""
        code = self._normalize_stock_code(stock_code)
        logged_in = False
        try:
            if not already_logged_in:
                _baostock_login()
                logged_in = True
            rs = bs.query_history_k_data_plus(
                code,
                "date,code,open,high,low,close,volume,amount,pctChg",
                start_date=start_date,
                end_date=end_date,
                frequency="d",
                adjustflag="2",
            )
            rows = []
            while rs.error_code == "0" and rs.next():
                rows.append(rs.get_row_data())
            if not rows:
                return None
            df = pd.DataFrame(rows, columns=rs.fields)
            df = df.rename(columns={"date": "trade_date", "code": "stock_code", "pctChg": "pct_change"})
            df["trade_date"] = pd.to_datetime(df["trade_date"])
            for col in ("open", "high", "low", "close", "volume", "amount", "pct_change"):
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df.sort_values("trade_date")
            if "pct_change" not in df.columns or df["pct_change"].isna().all():
                df["pct_change"] = df["close"].pct_change() * 100
            return df
        except Exception as e:
            logger.error(f"Fetch failed for {stock_code}: {e}")
            return None
        finally:
            if logged_in:
                _baostock_logout()

    def _fetch_remote(self, stock_code: str, start_date: str, end_date: str, already_logged_in: bool = False) -> pd.DataFrame | None:
        """Fetch single stock: baostock (free) → rqdatac (optional)."""
        result = self._fetch_remote_bs(stock_code, start_date, end_date, already_logged_in)
        if result is not None:
            return result
        rq_result = self._fetch_remote_rq([stock_code], start_date, end_date)
        if rq_result:
            return rq_result.get(self._normalize_stock_code(stock_code))
        return None

    # --- PLACEHOLDER_FETCH_STOCKS ---

    def fetch_stocks(
        self,
        stock_codes: list[str],
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame | None:
        """Fetch multiple stocks with caching. rqdatac batch → baostock fallback."""
        if adata_backend.is_enabled() and not CACHE_ONLY:
            return adata_backend.fetch_stocks(stock_codes, start_date, end_date)

        all_data: list[pd.DataFrame] = []
        to_fetch: list[str] = []

        req_start, req_end = pd.Timestamp(start_date), pd.Timestamp(end_date)

        for code in stock_codes:
            cached = self._load_cache(code)
            if cached is not None and len(cached) > 0:
                cache_min = cached["trade_date"].min()
                cache_max = cached["trade_date"].max()
                # Only use cache if it covers the full requested range
                # Allow 5-day tolerance for weekends/holidays at boundaries
                if cache_min <= req_start + pd.Timedelta(days=5) and cache_max >= req_end - pd.Timedelta(days=5):
                    filtered = cached[(cached["trade_date"] >= req_start) & (cached["trade_date"] <= req_end)]
                    if len(filtered) > 0:
                        all_data.append(filtered)
                        continue
            to_fetch.append(code)

        if to_fetch:
            if CACHE_ONLY:
                logger.warning(f"Cache-only mode: {len(to_fetch)} stocks not cached, skipping fetch")
            else:
                # Primary: baostock batch (free, no account needed)
                bs_fetched = set()
                if HAS_BAOSTOCK:
                    logger.info(f"[baostock] Fetching {len(to_fetch)} stocks...")
                    with _bs_lock:
                        _baostock_login()
                        try:
                            for code in to_fetch:
                                df = self._fetch_remote_bs(code, start_date, end_date, already_logged_in=True)
                                if df is not None and len(df) > 0:
                                    existing = self._load_cache(code)
                                    if existing is not None:
                                        df = pd.concat([existing, df]).drop_duplicates("trade_date", keep="last").sort_values("trade_date")
                                    self._save_cache(code, df)
                                    filtered = df[(df["trade_date"] >= req_start) & (df["trade_date"] <= req_end)]
                                    if len(filtered) > 0:
                                        all_data.append(filtered)
                                    bs_fetched.add(self._normalize_stock_code(code))
                        finally:
                            _baostock_logout()

                # Fallback: rqdatac for remaining stocks (optional, paid)
                rq_remaining = [c for c in to_fetch if self._normalize_stock_code(c) not in bs_fetched]
                if rq_remaining and _rqdatac_init():
                    for i in range(0, len(rq_remaining), 200):
                        chunk = rq_remaining[i:i+200]
                        rq_results = self._fetch_remote_rq(chunk, start_date, end_date)
                        for bs_code, df in rq_results.items():
                            if df is not None and len(df) > 0:
                                existing = self._load_cache(bs_code)
                                if existing is not None:
                                    df = pd.concat([existing, df]).drop_duplicates("trade_date", keep="last").sort_values("trade_date")
                                self._save_cache(bs_code, df)
                                filtered = df[(df["trade_date"] >= req_start) & (df["trade_date"] <= req_end)]
                                if len(filtered) > 0:
                                    all_data.append(filtered)

        if all_data:
            result = pd.concat(all_data, ignore_index=True)
            if "amount" in result.columns and "volume" in result.columns:
                raw_vol = result["volume"].replace(0, np.nan)
                result["vwap"] = result["amount"] / raw_vol
            logger.info(f"Loaded {len(result):,} records for {result['stock_code'].nunique()} stocks")
            return result
        return None

    def calculate_forward_returns(self, df: pd.DataFrame, periods: list[int] = None) -> pd.DataFrame:
        """Add fwd_ret_{N}d columns."""
        periods = periods or [5]
        df = df.sort_values(["stock_code", "trade_date"])
        for p in periods:
            df[f"fwd_ret_{p}d"] = df.groupby("stock_code")["close"].transform(
                lambda x: x.shift(-p) / x - 1
            )
        return df


# --- PLACEHOLDER_BENCHMARK ---


def fetch_benchmark_returns(
    benchmark: str = "hs300",
    start_date: str | None = None,
    end_date: str | None = None,
    cache_dir: str | None = None,
) -> pd.Series | None:
    """Fetch benchmark index daily returns: cache → rqdatac → baostock."""
    info = BENCHMARK_CODES.get(benchmark, BENCHMARK_CODES["hs300"])
    if adata_backend.is_enabled() and not CACHE_ONLY and start_date:
        resolved = benchmark if benchmark in BENCHMARK_CODES else "hs300"
        return adata_backend.fetch_benchmark_returns(resolved, info["name"], start_date, end_date)

    cache_dir = cache_dir or str(_PROJECT_ROOT / "data" / "benchmark")
    os.makedirs(cache_dir, exist_ok=True)

    cache_path = os.path.join(cache_dir, f"benchmark_{benchmark}.parquet")

    # Try cache first
    if os.path.exists(cache_path):
        try:
            df = pd.read_parquet(cache_path)
            df["trade_date"] = pd.to_datetime(df["trade_date"])
            df = df.sort_values("trade_date")
            cache_min = df["trade_date"].min()
            cache_max = df["trade_date"].max()
            req_s = pd.Timestamp(start_date) if start_date else cache_min
            req_e = pd.Timestamp(end_date) if end_date else cache_max
            # Only use cache if it covers the full requested range
            if cache_min <= req_s + pd.Timedelta(days=5) and cache_max >= req_e - pd.Timedelta(days=5):
                ret = df.set_index("trade_date")["daily_return"].dropna()
                ret.name = info["name"]
                if start_date:
                    ret = ret[ret.index >= pd.Timestamp(start_date)]
                if end_date:
                    ret = ret[ret.index <= pd.Timestamp(end_date)]
                if len(ret) > 1:
                    return ret
        except Exception:
            pass

    if CACHE_ONLY:
        logger.warning(f"Cache-only mode: benchmark {benchmark} not cached, returning None")
        return None

    start_date = start_date or (datetime.now() - timedelta(days=365 * 5)).strftime("%Y-%m-%d")
    end_date = end_date or datetime.now().strftime("%Y-%m-%d")

    # Try rqdatac
    rq_code = info.get("rqdatac")
    if rq_code and _rqdatac_init():
        try:
            rq_df = rqdatac.get_price(
                rq_code,
                start_date=start_date,
                end_date=end_date,
                frequency='1d',
                adjust_type='pre',
                expect_df=True,
            )
            if rq_df is not None and len(rq_df) > 0:
                rq_df = rq_df.reset_index()
                if "date" in rq_df.columns:
                    rq_df = rq_df.rename(columns={"date": "trade_date"})
                rq_df["trade_date"] = pd.to_datetime(rq_df["trade_date"])
                rq_df["close"] = pd.to_numeric(rq_df["close"], errors="coerce")
                rq_df = rq_df.sort_values("trade_date")
                rq_df["daily_return"] = rq_df["close"].pct_change()
                rq_df[["trade_date", "close", "daily_return"]].to_parquet(cache_path, index=False)
                ret = rq_df.set_index("trade_date")["daily_return"].dropna()
                ret.name = info["name"]
                logger.info(f"[rqdatac] Benchmark {benchmark}: {len(ret)} days")
                return ret
        except Exception as e:
            logger.warning(f"rqdatac benchmark fetch failed: {e}, falling back to baostock")

    # Fallback: baostock
    bs_code = info["baostock"]
    with _bs_lock:
        _baostock_login()
        try:
            rs = bs.query_history_k_data_plus(
                bs_code,
                "date,close",
                start_date=start_date,
                end_date=end_date,
                frequency="d",
                adjustflag="2",
            )
            rows = []
            while rs.error_code == "0" and rs.next():
                rows.append(rs.get_row_data())
            if not rows:
                return None
            df = pd.DataFrame(rows, columns=rs.fields)
            df["trade_date"] = pd.to_datetime(df["date"])
            df["close"] = pd.to_numeric(df["close"], errors="coerce")
            df = df.sort_values("trade_date")
            df["daily_return"] = df["close"].pct_change()
            df[["trade_date", "close", "daily_return"]].to_parquet(cache_path, index=False)
            ret = df.set_index("trade_date")["daily_return"].dropna()
            ret.name = info["name"]
            logger.info(f"[baostock] Benchmark {benchmark}: {len(ret)} days")
            return ret
        finally:
            _baostock_logout()


def _fetch_akshare(bs_code: str, start_date: str, end_date: str) -> pd.DataFrame | None:
    """Fetch single stock daily data from akshare (东方财富).

    Args:
        bs_code: baostock format code, e.g. "sh.600519"
        start_date: "YYYY-MM-DD"
        end_date: "YYYY-MM-DD"

    Returns DataFrame with columns matching cache format, or None.
    """
    try:
        import akshare as ak
    except ImportError:
        return None

    try:
        # Convert bs_code (sh.600519) to akshare symbol (600519)
        symbol = bs_code.split(".")[1]
        ak_start = start_date.replace("-", "")
        ak_end = end_date.replace("-", "")

        df = ak.stock_zh_a_hist(
            symbol=symbol, period="daily",
            start_date=ak_start, end_date=ak_end, adjust="qfq",
        )
        if df is None or len(df) == 0:
            return None

        df = df.rename(columns={
            "日期": "trade_date",
            "开盘": "open",
            "最高": "high",
            "最低": "low",
            "收盘": "close",
            "成交量": "volume",
            "成交额": "amount",
            "涨跌幅": "pct_change",
        })
        df["stock_code"] = bs_code
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        for col in ("open", "high", "low", "close", "volume", "amount", "pct_change"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df[["trade_date", "stock_code", "open", "high", "low", "close", "volume", "amount", "pct_change"]]
        return df.sort_values("trade_date")
    except Exception as e:
        logger.warning(f"[akshare] Failed to fetch {bs_code}: {e}")
        return None


def refresh_all_cached_stocks():
    """Refresh all cached stock data with incremental updates.

    Data source priority: akshare (same-day data) → baostock (fallback).
    rqdatac is globally disabled, only manual prefetch can use it.
    Designed to run as a daily cron job after market close (e.g. 15:10 CST).
    """
    _refresh_all_cached_stocks_impl()


def _refresh_all_cached_stocks_impl():
    """Incremental refresh implementation: akshare → baostock."""
    fetcher = MarketDataFetcher()
    cache_dir = fetcher.stock_cache_dir
    today = datetime.now().strftime("%Y-%m-%d")

    # Collect all cached stock codes and their last dates
    parquet_files = [f for f in os.listdir(cache_dir) if f.endswith(".parquet")]
    if not parquet_files:
        logger.info("[refresh] No cached stocks found, skipping")
        return

    stocks_to_update: list[tuple] = []  # (bs_code, start_date)
    for fname in parquet_files:
        bs_code = fname.replace(".parquet", "").replace("_", ".", 1)  # sh_600519 → sh.600519
        try:
            df = pd.read_parquet(os.path.join(cache_dir, fname))
            last_date = pd.to_datetime(df["trade_date"]).max()
            # Need update if last cached date is before today
            if last_date.strftime("%Y-%m-%d") < today:
                # Fetch from the day after last cached date
                fetch_start = (last_date + timedelta(days=1)).strftime("%Y-%m-%d")
                stocks_to_update.append((bs_code, fetch_start))
        except Exception as e:
            logger.warning(f"[refresh] Failed to read {fname}: {e}")
            continue

    if not stocks_to_update:
        logger.info("[refresh] All stocks up to date")
        return

    logger.info(f"[refresh] Updating {len(stocks_to_update)} stocks up to {today}")

    updated = 0
    failed = 0
    bs_fallback = 0

    # Phase 1: Try akshare (supports same-day data)
    bs_remaining: list[tuple] = []
    for bs_code, start_date in stocks_to_update:
        try:
            new_data = _fetch_akshare(bs_code, start_date, today)
            if new_data is not None and len(new_data) > 0:
                existing = fetcher._load_cache(bs_code)
                if existing is not None:
                    merged = pd.concat([existing, new_data]).drop_duplicates("trade_date", keep="last").sort_values("trade_date")
                else:
                    merged = new_data
                fetcher._save_cache(bs_code, merged)
                updated += 1
            else:
                bs_remaining.append((bs_code, start_date))
        except Exception as e:
            logger.warning(f"[refresh] akshare failed for {bs_code}: {e}")
            bs_remaining.append((bs_code, start_date))

    # Phase 2: Fallback to baostock for remaining stocks
    if bs_remaining and HAS_BAOSTOCK:
        logger.info(f"[refresh] Falling back to baostock for {len(bs_remaining)} stocks")
        with _bs_lock:
            _baostock_login()
            try:
                for bs_code, start_date in bs_remaining:
                    try:
                        new_data = fetcher._fetch_remote_bs(bs_code, start_date, today, already_logged_in=True)
                        if new_data is not None and len(new_data) > 0:
                            existing = fetcher._load_cache(bs_code)
                            if existing is not None:
                                merged = pd.concat([existing, new_data]).drop_duplicates("trade_date", keep="last").sort_values("trade_date")
                            else:
                                merged = new_data
                            fetcher._save_cache(bs_code, merged)
                            updated += 1
                            bs_fallback += 1
                    except Exception as e:
                        logger.warning(f"[refresh] baostock failed for {bs_code}: {e}")
                        failed += 1
            finally:
                _baostock_logout()

    no_data = len(stocks_to_update) - updated - failed
    logger.info(f"[refresh] Done: {updated} updated (akshare: {updated - bs_fallback}, baostock: {bs_fallback}), {failed} failed, {no_data} no new data")


def refresh_all_stocks_full(stock_codes: list[str] | None = None, start_date: str | None = None, end_date: str | None = None):
    """Full refresh via rqdatac. Manual trigger only.

    Fetches complete history for given stocks (or all cached stocks) and
    overwrites the cache. Uses rqdatac batch API for efficiency.
    """
    if not _rqdatac_init():
        raise RuntimeError("rqdatac not available — check credentials")

    fetcher = MarketDataFetcher()
    cache_dir = fetcher.stock_cache_dir
    today = end_date or datetime.now().strftime("%Y-%m-%d")
    start = start_date or "2020-01-01"

    # Default: all cached stocks
    if stock_codes is None:
        parquet_files = [f for f in os.listdir(cache_dir) if f.endswith(".parquet")]
        stock_codes = [f.replace(".parquet", "").replace("_", ".", 1) for f in parquet_files]

    if not stock_codes:
        logger.info("[full_refresh] No stocks to update")
        return

    logger.info(f"[full_refresh] rqdatac full refresh: {len(stock_codes)} stocks, {start} to {today}")

    updated = 0
    failed = 0
    chunk_size = 50  # rqdatac batch limit

    for i in range(0, len(stock_codes), chunk_size):
        chunk = stock_codes[i:i + chunk_size]
        try:
            results = fetcher._fetch_remote_rq(chunk, start, today)
            for bs_code, df in results.items():
                if df is not None and len(df) > 0:
                    fetcher._save_cache(bs_code, df)
                    updated += 1
        except Exception as e:
            logger.warning(f"[full_refresh] Chunk failed: {e}")
            failed += len(chunk)

        if (i + chunk_size) % 500 == 0:
            logger.info(f"[full_refresh] Progress: {min(i + chunk_size, len(stock_codes))}/{len(stock_codes)}")

    logger.info(f"[full_refresh] Done: {updated} updated, {failed} failed")


def refresh_all_stocks_rqdatac_incremental():
    """Incremental refresh via rqdatac. Manual trigger only.

    Same logic as the daily akshare/baostock refresh, but uses rqdatac
    as the data source. Only fetches data from last cached date to today.
    """
    if not _rqdatac_init():
        raise RuntimeError("rqdatac not available — check credentials")

    fetcher = MarketDataFetcher()
    cache_dir = fetcher.stock_cache_dir
    today = datetime.now().strftime("%Y-%m-%d")

    parquet_files = [f for f in os.listdir(cache_dir) if f.endswith(".parquet")]
    if not parquet_files:
        logger.info("[rq_incr] No cached stocks found")
        return

    stocks_to_update: list[tuple] = []
    for fname in parquet_files:
        bs_code = fname.replace(".parquet", "").replace("_", ".", 1)
        try:
            df = pd.read_parquet(os.path.join(cache_dir, fname))
            last_date = pd.to_datetime(df["trade_date"]).max()
            if last_date.strftime("%Y-%m-%d") < today:
                fetch_start = (last_date + timedelta(days=1)).strftime("%Y-%m-%d")
                stocks_to_update.append((bs_code, fetch_start))
        except Exception:
            continue

    if not stocks_to_update:
        logger.info("[rq_incr] All stocks up to date")
        return

    logger.info(f"[rq_incr] rqdatac incremental: {len(stocks_to_update)} stocks to {today}")

    updated = 0
    failed = 0
    chunk_size = 50

    # Group by start_date for efficient batch fetching
    from collections import defaultdict
    by_start: defaultdict[str, list] = defaultdict(list)
    for bs_code, start_date in stocks_to_update:
        by_start[start_date].append(bs_code)

    for start_date, codes in by_start.items():
        for i in range(0, len(codes), chunk_size):
            chunk = codes[i:i + chunk_size]
            try:
                results = fetcher._fetch_remote_rq(chunk, start_date, today)
                for bs_code, df in results.items():
                    if df is not None and len(df) > 0:
                        existing = fetcher._load_cache(bs_code)
                        if existing is not None:
                            merged = pd.concat([existing, df]).drop_duplicates("trade_date", keep="last").sort_values("trade_date")
                        else:
                            merged = df
                        fetcher._save_cache(bs_code, merged)
                        updated += 1
            except Exception as e:
                logger.warning(f"[rq_incr] Chunk failed: {e}")
                failed += len(chunk)

        logger.info(f"[rq_incr] start_date={start_date}: processed {len(codes)} stocks")

    logger.info(f"[rq_incr] Done: {updated} updated, {failed} failed, {len(stocks_to_update) - updated - failed} no new data")
