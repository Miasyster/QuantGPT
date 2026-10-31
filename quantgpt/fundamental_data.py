"""Fundamental data fetcher with rqdatac (primary) + baostock (fallback) + Parquet caching.

rqdatac path: uses get_factor() for daily-frequency financial indicators (no alignment needed).
baostock path: fetches quarterly data from 6 APIs, aligns to daily via pubDate merge_asof.
"""

import logging
import re
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Variable registry: user-facing name -> (api_name, baostock_field)
# ---------------------------------------------------------------------------

FUNDAMENTAL_VARIABLES: dict[str, tuple[str, str]] = {
    # profit API
    "roe":              ("profit", "roeAvg"),
    "np_margin":        ("profit", "npMargin"),
    "gp_margin":        ("profit", "gpMargin"),
    "net_profit":       ("profit", "netProfit"),
    "eps_ttm":          ("profit", "epsTTM"),
    "revenue":          ("profit", "MBRevenue"),
    "total_share":      ("profit", "totalShare"),
    "float_share":      ("profit", "liqaShare"),
    # growth API
    "yoy_ni":           ("growth", "YOYNI"),
    "yoy_equity":       ("growth", "YOYEquity"),
    "yoy_asset":        ("growth", "YOYAsset"),
    "yoy_pni":          ("growth", "YOYPNI"),
    # balance API
    "current_ratio":    ("balance", "currentRatio"),
    "debt_ratio":       ("balance", "liabilityToAsset"),
    "equity_multiplier": ("balance", "assetToEquity"),
    # operation API
    "asset_turnover":   ("operation", "AssetTurnRatio"),
    "inv_turnover":     ("operation", "INVTurnRatio"),
    # dupont API
    "dupont_roe":       ("dupont", "dupontROE"),
    "dupont_asset_turn": ("dupont", "dupontAssetTurn"),
    # cash_flow API
    "cfo_to_np":        ("cash_flow", "CFOToNP"),
}

# Derived variables computed from close + fundamental columns
DERIVED_VARIABLES: dict[str, list[str]] = {
    "pe": ["net_profit", "total_share"],         # close * total_share / net_profit
    "pb": ["net_profit", "total_share", "roe"],   # close * total_share / (net_profit / roe)
    "ps": ["revenue", "total_share"],             # close * total_share / revenue
    "roa": ["roe", "equity_multiplier"],          # roe / equity_multiplier
    "bps": ["net_profit", "total_share", "roe"],  # (net_profit / roe) / total_share
    "nav": ["net_profit", "roe"],                 # net_profit / roe (净资产)
}

ALL_FUNDAMENTAL_NAMES: frozenset = frozenset(FUNDAMENTAL_VARIABLES.keys()) | frozenset(DERIVED_VARIABLES.keys()) | frozenset(["dividend_yield"])

# Reverse map: baostock field -> user-facing name
_BS_TO_USER: dict[str, str] = {v[1]: k for k, v in FUNDAMENTAL_VARIABLES.items()}

# API name -> baostock function name
_API_FUNC_MAP = {
    "profit":    "query_profit_data",
    "growth":    "query_growth_data",
    "balance":   "query_balance_data",
    "operation": "query_operation_data",
    "dupont":    "query_dupont_data",
    "cash_flow": "query_cash_flow_data",
}

# Fields to request per API (only the ones we need + pub/stat dates)
_API_FIELDS: dict[str, list[str]] = {
    "profit":    ["code", "pubDate", "statDate", "roeAvg", "npMargin", "gpMargin",
                  "netProfit", "epsTTM", "MBRevenue", "totalShare", "liqaShare"],
    "growth":    ["code", "pubDate", "statDate", "YOYNI", "YOYEquity", "YOYAsset", "YOYPNI"],
    "balance":   ["code", "pubDate", "statDate", "currentRatio", "liabilityToAsset", "assetToEquity"],
    "operation": ["code", "pubDate", "statDate", "AssetTurnRatio", "INVTurnRatio"],
    "dupont":    ["code", "pubDate", "statDate", "dupontROE", "dupontAssetTurn"],
    "cash_flow": ["code", "pubDate", "statDate", "CFOToNP"],
}


def detect_fundamental_vars(expression: str) -> set[str]:
    """Scan expression for fundamental variable names. Returns set of matched names."""
    tokens = set(re.findall(r'\b[a-z_]+\b', expression.lower()))
    return tokens & ALL_FUNDAMENTAL_NAMES


def get_needed_apis(var_names: set[str]) -> set[str]:
    """Given variable names, return the set of baostock API names to call."""
    # Expand derived variables to their dependencies
    expanded = set()
    for v in var_names:
        if v in DERIVED_VARIABLES:
            expanded.update(DERIVED_VARIABLES[v])
        elif v in FUNDAMENTAL_VARIABLES:
            expanded.add(v)
    # Map to API names
    apis = set()
    for v in expanded:
        if v in FUNDAMENTAL_VARIABLES:
            apis.add(FUNDAMENTAL_VARIABLES[v][0])
    return apis


def _quarter_range(start_date: str, end_date: str) -> list[tuple[int, int]]:
    """Generate (year, quarter) pairs covering the date range.

    Starts 1 year before start_date to ensure pubDate coverage
    (Q4 reports publish in Apr of next year).
    """
    from datetime import datetime as dt
    start = dt.strptime(start_date[:10], "%Y-%m-%d")
    end = dt.strptime(end_date[:10], "%Y-%m-%d")
    # Go back 1 year for publication lag
    first_year = start.year - 1
    last_year = end.year
    quarters = []
    for y in range(first_year, last_year + 1):
        for q in range(1, 5):
            quarters.append((y, q))
    return quarters


class FundamentalDataFetcher:
    """Quarterly financial data fetcher with per-stock Parquet caching."""

    def __init__(self):
        self.cache_dir = _PROJECT_ROOT / "data" / "fundamentals"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, stock_code: str) -> Path:
        normalized = stock_code.replace(".", "_")
        return self.cache_dir / f"{normalized}.parquet"

    def _load_cache(self, stock_code: str) -> pd.DataFrame | None:
        path = self._cache_path(stock_code)
        if not path.exists():
            return None
        try:
            df = pd.read_parquet(path)
            if "pub_date" in df.columns:
                df["pub_date"] = pd.to_datetime(df["pub_date"])
            if "stat_date" in df.columns:
                df["stat_date"] = pd.to_datetime(df["stat_date"])
            return df
        except Exception:
            return None

    def _save_cache(self, stock_code: str, df: pd.DataFrame):
        if df is None or len(df) == 0:
            return
        path = self._cache_path(stock_code)
        try:
            df.to_parquet(path, index=False)
        except Exception as e:
            logger.warning(f"Failed to save fundamental cache for {stock_code}: {e}")

    def _fetch_single_api(self, code: str, year: int, quarter: int, api_name: str) -> pd.DataFrame | None:
        """Fetch one baostock financial API for one stock-quarter."""
        try:
            import baostock as bs
        except ImportError:
            return None

        func = getattr(bs, _API_FUNC_MAP[api_name])
        rs = func(code=code, year=year, quarter=quarter)
        if rs.error_code != "0":
            return None

        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        if not rows:
            return None

        df = pd.DataFrame(rows, columns=rs.fields)
        return df

    def _fetch_stock(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
        needed_apis: set[str],
    ) -> pd.DataFrame | None:
        """Fetch all quarterly data for one stock, merge across APIs by statDate."""
        quarters = _quarter_range(start_date, end_date)

        # Fetch each API
        api_dfs: dict[str, list[pd.DataFrame]] = {api: [] for api in needed_apis}
        for year, quarter in quarters:
            for api_name in needed_apis:
                result = self._fetch_single_api(stock_code, year, quarter, api_name)
                if result is not None and len(result) > 0:
                    api_dfs[api_name].append(result)

        # Concat per API
        merged_parts = []
        for api_name, dfs in api_dfs.items():
            if not dfs:
                continue
            api_df = pd.concat(dfs, ignore_index=True)
            # Keep only our needed fields
            keep_cols = [c for c in _API_FIELDS[api_name] if c in api_df.columns]
            api_df = api_df[keep_cols].copy()
            merged_parts.append(api_df)

        if not merged_parts:
            return None

        # Merge all API results on (code, pubDate, statDate)
        result = merged_parts[0]
        for part in merged_parts[1:]:
            # Avoid duplicate columns in merge
            merge_on = ["code", "pubDate", "statDate"]
            extra_cols = [c for c in part.columns if c not in result.columns]
            if extra_cols:
                result = result.merge(part[merge_on + extra_cols], on=merge_on, how="outer")

        # Rename columns to user-facing names
        rename_map = {"pubDate": "pub_date", "statDate": "stat_date", "code": "stock_code"}
        for bs_field, user_name in _BS_TO_USER.items():
            if bs_field in result.columns:
                rename_map[bs_field] = user_name
        result = result.rename(columns=rename_map)

        # Convert numeric columns
        for col in result.columns:
            if col in ("stock_code", "pub_date", "stat_date"):
                continue
            result[col] = pd.to_numeric(result[col], errors="coerce")

        # Parse dates
        result["pub_date"] = pd.to_datetime(result["pub_date"], errors="coerce")
        result["stat_date"] = pd.to_datetime(result["stat_date"], errors="coerce")

        # Drop rows with no pub_date (unusable)
        result = result.dropna(subset=["pub_date"])

        # Deduplicate on (stock_code, stat_date), keep latest pub_date
        result = result.sort_values("pub_date").drop_duplicates(
            subset=["stock_code", "stat_date"], keep="last"
        )

        return result if len(result) > 0 else None

    def fetch_fundamentals(
        self,
        stock_codes: list[str],
        start_date: str,
        end_date: str,
        needed_vars: set[str],
    ) -> pd.DataFrame | None:
        """Fetch fundamental data for multiple stocks with caching."""
        needed_apis = get_needed_apis(needed_vars)
        if not needed_apis:
            return None

        from .market_data import CACHE_ONLY, _baostock_login, _baostock_logout, _bs_lock

        all_dfs = []

        # First pass: load all cached data
        to_fetch = []
        for code in stock_codes:
            cached = self._load_cache(code)
            if cached is not None:
                raw_vars = set()
                for v in needed_vars:
                    if v in DERIVED_VARIABLES:
                        raw_vars.update(DERIVED_VARIABLES[v])
                    elif v in FUNDAMENTAL_VARIABLES:
                        raw_vars.add(v)
                has_all_cols = all(v in cached.columns for v in raw_vars)

                if has_all_cols:
                    cache_min = cached["stat_date"].min()
                    cache_max = cached["stat_date"].max()
                    req_start = pd.Timestamp(start_date) - pd.Timedelta(days=365)
                    req_end = pd.Timestamp(end_date)
                    if cache_min <= req_start + pd.Timedelta(days=100) and cache_max >= req_end - pd.Timedelta(days=100):
                        all_dfs.append(cached)
                        continue
            to_fetch.append((code, cached))

        # Second pass: fetch uncached from baostock (unless cache-only)
        if to_fetch:
            if CACHE_ONLY:
                logger.warning(f"Cache-only mode: {len(to_fetch)} stocks fundamentals not cached, skipping fetch")
            else:
                with _bs_lock:
                    _baostock_login()
                    try:
                        for i, (code, cached) in enumerate(to_fetch):
                            if (i + 1) % 50 == 0:
                                logger.info(f"Fetching fundamentals: {i+1}/{len(to_fetch)}")
                            try:
                                stock_df = self._fetch_stock(code, start_date, end_date, needed_apis)
                                if stock_df is not None and len(stock_df) > 0:
                                    if cached is not None:
                                        combined = pd.concat([cached, stock_df], ignore_index=True)
                                        combined = combined.sort_values("pub_date").drop_duplicates(
                                            subset=["stock_code", "stat_date"], keep="last"
                                        )
                                        stock_df = combined
                                    self._save_cache(code, stock_df)
                                    all_dfs.append(stock_df)
                            except Exception as e:
                                logger.warning(f"Failed to fetch fundamentals for {code}: {e}")
                    finally:
                        _baostock_logout()

        if not all_dfs:
            return None

        result = pd.concat(all_dfs, ignore_index=True)
        return result if len(result) > 0 else None

    def align_to_daily(
        self,
        quarterly_df: pd.DataFrame,
        market_df: pd.DataFrame,
        needed_vars: set[str],
    ) -> pd.DataFrame:
        """Align quarterly data to daily using pubDate (point-in-time, no look-ahead).

        Uses pd.merge_asof with direction='backward': for each trading day T,
        use the most recent quarterly data where pubDate <= T.
        Then compute derived variables (pe, pb, ps).
        """
        # Determine which raw columns we need from quarterly_df
        raw_cols = set()
        for v in needed_vars:
            if v in DERIVED_VARIABLES:
                raw_cols.update(DERIVED_VARIABLES[v])
            elif v in FUNDAMENTAL_VARIABLES:
                raw_cols.add(v)

        # Filter quarterly_df to needed columns
        keep_cols = ["stock_code", "pub_date"] + [c for c in raw_cols if c in quarterly_df.columns]
        qdf = quarterly_df[keep_cols].copy()
        qdf = qdf.dropna(subset=["pub_date"])

        # merge_asof requires the key column to be sorted.
        # Since we merge by stock_code, do it per-stock to avoid cross-stock sorting issues.
        market_df = market_df.copy()
        # merge_asof also rejects keys of different datetime precision (ns vs us)
        market_df["trade_date"] = pd.to_datetime(market_df["trade_date"]).astype("datetime64[ns]")
        qdf["pub_date"] = pd.to_datetime(qdf["pub_date"]).astype("datetime64[ns]")
        result_parts = []
        for code, mkt_group in market_df.groupby("stock_code", sort=False):
            fund_group = qdf[qdf["stock_code"] == code].sort_values("pub_date")
            if len(fund_group) == 0:
                result_parts.append(mkt_group)
                continue
            mkt_sorted = mkt_group.sort_values("trade_date")
            merged_group = pd.merge_asof(
                mkt_sorted,
                fund_group.drop(columns=["stock_code"]),
                left_on="trade_date",
                right_on="pub_date",
                direction="backward",
            )
            result_parts.append(merged_group)

        if not result_parts:
            return market_df
        merged = pd.concat(result_parts, ignore_index=True)

        # Compute derived variables
        if "pe" in needed_vars:
            with np.errstate(divide="ignore", invalid="ignore"):
                merged["pe"] = np.where(
                    (merged.get("net_profit", 0) != 0) & merged.get("net_profit", pd.Series(dtype=float)).notna(),
                    merged["close"] * merged.get("total_share", np.nan) / merged.get("net_profit", np.nan),
                    np.nan,
                )
        if "pb" in needed_vars:
            with np.errstate(divide="ignore", invalid="ignore"):
                roe_val = merged.get("roe", pd.Series(dtype=float))
                net_profit_val = merged.get("net_profit", pd.Series(dtype=float))
                total_share_val = merged.get("total_share", pd.Series(dtype=float))
                # book value = net_profit / roe (annualized equity approximation)
                book_value = np.where(
                    (roe_val != 0) & roe_val.notna(),
                    net_profit_val / roe_val,
                    np.nan,
                )
                merged["pb"] = np.where(
                    (book_value != 0) & pd.notna(book_value),
                    merged["close"] * total_share_val / book_value,
                    np.nan,
                )
        if "ps" in needed_vars:
            with np.errstate(divide="ignore", invalid="ignore"):
                merged["ps"] = np.where(
                    (merged.get("revenue", 0) != 0) & merged.get("revenue", pd.Series(dtype=float)).notna(),
                    merged["close"] * merged.get("total_share", np.nan) / merged.get("revenue", np.nan),
                    np.nan,
                )
        if "roa" in needed_vars:
            with np.errstate(divide="ignore", invalid="ignore"):
                eq_mult = merged.get("equity_multiplier", pd.Series(dtype=float))
                merged["roa"] = np.where(
                    (eq_mult != 0) & eq_mult.notna(),
                    merged.get("roe", np.nan) / eq_mult,
                    np.nan,
                )
        if "bps" in needed_vars:
            with np.errstate(divide="ignore", invalid="ignore"):
                roe_val = merged.get("roe", pd.Series(dtype=float))
                net_profit_val = merged.get("net_profit", pd.Series(dtype=float))
                total_share_val = merged.get("total_share", pd.Series(dtype=float))
                book_value = np.where(
                    (roe_val != 0) & roe_val.notna(),
                    net_profit_val / roe_val,
                    np.nan,
                )
                merged["bps"] = np.where(
                    (total_share_val != 0) & pd.notna(total_share_val) & pd.notna(book_value),
                    book_value / total_share_val,
                    np.nan,
                )
        if "nav" in needed_vars:
            with np.errstate(divide="ignore", invalid="ignore"):
                roe_val = merged.get("roe", pd.Series(dtype=float))
                net_profit_val = merged.get("net_profit", pd.Series(dtype=float))
                merged["nav"] = np.where(
                    (roe_val != 0) & roe_val.notna(),
                    net_profit_val / roe_val,
                    np.nan,
                )

        # Drop the pub_date column (no longer needed)
        if "pub_date" in merged.columns:
            merged = merged.drop(columns=["pub_date"])

        return merged

    # ------------------------------------------------------------------
    # Dividend data (event-based, separate from quarterly financials)
    # ------------------------------------------------------------------

    def _dividend_cache_dir(self) -> Path:
        d = _PROJECT_ROOT / "data" / "dividends"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _dividend_cache_path(self, stock_code: str) -> Path:
        normalized = stock_code.replace(".", "_")
        return self._dividend_cache_dir() / f"{normalized}.parquet"

    def _load_dividend_cache(self, stock_code: str) -> pd.DataFrame | None:
        path = self._dividend_cache_path(stock_code)
        if not path.exists():
            return None
        try:
            df = pd.read_parquet(path)
            if "ex_date" in df.columns:
                df["ex_date"] = pd.to_datetime(df["ex_date"])
            return df
        except Exception:
            return None

    def _save_dividend_cache(self, stock_code: str, df: pd.DataFrame):
        if df is None or len(df) == 0:
            return
        try:
            df.to_parquet(self._dividend_cache_path(stock_code), index=False)
        except Exception as e:
            logger.warning(f"Failed to save dividend cache for {stock_code}: {e}")

    def _fetch_stock_dividends(self, code: str, start_date: str, end_date: str) -> pd.DataFrame | None:
        """Fetch dividend events for one stock across years."""
        try:
            import baostock as bs
        except ImportError:
            return None
        from datetime import datetime as dt
        start_year = dt.strptime(start_date[:10], "%Y-%m-%d").year - 1
        end_year = dt.strptime(end_date[:10], "%Y-%m-%d").year

        rows = []
        for year in range(start_year, end_year + 1):
            rs = bs.query_dividend_data(code=code, year=str(year), yearType="report")
            if rs.error_code != "0":
                continue
            while rs.next():
                row = rs.get_row_data()
                ex_date_str = row[6]  # dividOperateDate
                cash_ps_str = row[9]  # dividCashPsBeforeTax
                if not ex_date_str or not cash_ps_str:
                    continue
                try:
                    cash_ps = float(cash_ps_str)
                except (ValueError, TypeError):
                    continue
                if cash_ps <= 0:
                    continue
                rows.append({
                    "stock_code": code,
                    "ex_date": pd.Timestamp(ex_date_str),
                    "cash_per_share": cash_ps,
                })

        if not rows:
            return None
        df = pd.DataFrame(rows)
        df = df.drop_duplicates(subset=["stock_code", "ex_date", "cash_per_share"])
        # Same ex_date may appear from different report years; keep one with highest amount
        df = df.sort_values("cash_per_share", ascending=False).drop_duplicates(
            subset=["stock_code", "ex_date"], keep="first"
        )
        return df

    def fetch_dividend_data(
        self,
        stock_codes: list[str],
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame | None:
        """Fetch dividend data for multiple stocks with caching."""
        from .market_data import CACHE_ONLY, _baostock_login, _baostock_logout, _bs_lock

        all_dfs = []
        to_fetch_codes = []

        for code in stock_codes:
            cached = self._load_dividend_cache(code)
            if cached is not None and len(cached) > 0:
                all_dfs.append(cached)
            else:
                to_fetch_codes.append(code)

        if to_fetch_codes:
            if CACHE_ONLY:
                logger.warning(f"Cache-only mode: {len(to_fetch_codes)} stocks dividends not cached, skipping fetch")
            else:
                with _bs_lock:
                    _baostock_login()
                    try:
                        for i, code in enumerate(to_fetch_codes):
                            if (i + 1) % 50 == 0:
                                logger.info(f"Fetching dividends: {i+1}/{len(to_fetch_codes)}")
                            try:
                                div_df = self._fetch_stock_dividends(code, start_date, end_date)
                                if div_df is not None and len(div_df) > 0:
                                    self._save_dividend_cache(code, div_df)
                                    all_dfs.append(div_df)
                            except Exception as e:
                                logger.warning(f"Failed to fetch dividends for {code}: {e}")
                    finally:
                        _baostock_logout()

        if not all_dfs:
            return None
        return pd.concat(all_dfs, ignore_index=True)

    def align_dividends_to_daily(
        self,
        div_df: pd.DataFrame,
        market_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """Align dividend events to daily, compute TTM dividend yield.

        For each trading day, sum all dividends with ex_date in the past 365 days,
        then dividend_yield = ttm_cash_per_share / close.
        """
        market_df = market_df.copy()

        result_parts = []
        for code, mkt_group in market_df.groupby("stock_code", sort=False):
            stock_divs = div_df[div_df["stock_code"] == code].sort_values("ex_date")
            if len(stock_divs) == 0:
                mkt_group = mkt_group.copy()
                mkt_group["dividend_yield"] = np.nan
                result_parts.append(mkt_group)
                continue

            mkt_sorted = mkt_group.sort_values("trade_date").copy()
            # For each trade_date, compute TTM dividend (sum of cash_per_share
            # where ex_date is within [trade_date - 365d, trade_date])
            ttm_divs = []
            div_dates = stock_divs["ex_date"].values
            div_cash = stock_divs["cash_per_share"].values
            for td in mkt_sorted["trade_date"].values:
                td_ts = pd.Timestamp(td)
                cutoff = td_ts - pd.Timedelta(days=365)
                mask = (div_dates >= cutoff.to_numpy()) & (div_dates <= td_ts.to_numpy())
                ttm_divs.append(div_cash[mask].sum() if mask.any() else np.nan)

            mkt_sorted["_ttm_div"] = ttm_divs
            with np.errstate(divide="ignore", invalid="ignore"):
                mkt_sorted["dividend_yield"] = np.where(
                    (mkt_sorted["close"] != 0) & mkt_sorted["_ttm_div"].notna(),
                    mkt_sorted["_ttm_div"] / mkt_sorted["close"],
                    np.nan,
                )
            mkt_sorted.drop(columns=["_ttm_div"], inplace=True)
            result_parts.append(mkt_sorted)

        if not result_parts:
            return market_df
        return pd.concat(result_parts, ignore_index=True)


# ─── rqdatac daily factor enrichment + Parquet caching ─────────────

# Map project variable names → rqdatac factor names
_RQ_FACTOR_MAP: dict[str, str] = {
    # Profitability
    "roe":              "return_on_equity",
    "np_margin":        "net_profit_margin",
    "gp_margin":        "gross_profit_margin",
    "eps_ttm":          "earnings_per_share",
    # Growth
    "yoy_ni":           "inc_net_profit",
    "yoy_equity":       "inc_earnings_per_share",   # no direct equity growth; EPS growth as proxy
    "yoy_asset":        "inc_operating_revenue",     # no direct asset growth; revenue growth as proxy
    "yoy_pni":          "inc_net_profit",
    # Balance
    "current_ratio":    "current_ratio",
    "debt_ratio":       "debt_to_asset_ratio",
    "equity_multiplier": "equity_multiplier",
    # Operation
    "asset_turnover":   "total_asset_turnover",
    "inv_turnover":     "inventory_turnover",
    # Dupont (rqdatac has no DuPont-specific factors; use standard ROE / turnover)
    "dupont_roe":       "return_on_equity",
    "dupont_asset_turn": "total_asset_turnover",
    # Cash flow
    "cfo_to_np":        "operating_cash_flow_per_share",  # closest available
    # Valuation (rqdatac computes these directly as daily factors)
    "pe":               "pe_ratio",
    "pb":               "pb_ratio",
    "ps":               "ps_ratio",
    # Dividend
    "dividend_yield":   "dividend_yield",
    # Raw financials (for derived calculations)
    "net_profit":       "net_profit",
    "revenue":          "revenue",
    "total_share":      "total_equity",          # total_shares unavailable; total_equity as proxy
    "float_share":      "a_share_market_val",    # circulation_a_shares unavailable
}

# All unique rqdatac factor names (for prewarming)
ALL_RQ_FACTORS: list[str] = sorted(set(_RQ_FACTOR_MAP.values()))

# Reverse map: rqdatac name → project name(s). One rqdatac factor can map to multiple project vars.
_RQ_TO_VARS: dict[str, list[str]] = {}
for _var, _rq in _RQ_FACTOR_MAP.items():
    _RQ_TO_VARS.setdefault(_rq, []).append(_var)

# Single reverse map (first only) for backward compat
_RQ_TO_VAR: dict[str, str] = {rq: vars[0] for rq, vars in _RQ_TO_VARS.items()}

# Factor cache directory
_FACTOR_CACHE_DIR = _PROJECT_ROOT / "data" / "factors"


def _factor_cache_path(stock_code: str) -> Path:
    """Per-stock factor cache file path."""
    normalized = stock_code.replace(".", "_")
    return _FACTOR_CACHE_DIR / f"{normalized}.parquet"


def _load_factor_cache(stock_code: str, start_date: str, end_date: str) -> pd.DataFrame | None:
    """Load cached factor data for a stock if it covers the requested range."""
    path = _factor_cache_path(stock_code)
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        cache_min = df["trade_date"].min()
        cache_max = df["trade_date"].max()
        req_start = pd.Timestamp(start_date)
        req_end = pd.Timestamp(end_date)
        if cache_min <= req_start + pd.Timedelta(days=5) and cache_max >= req_end - pd.Timedelta(days=5):
            filtered = df[(df["trade_date"] >= req_start) & (df["trade_date"] <= req_end)]
            if len(filtered) > 0:
                return filtered
    except Exception as e:
        logger.warning(f"Factor cache load failed for {stock_code}: {e}")
    return None


def _save_factor_cache(stock_code: str, df: pd.DataFrame):
    """Save factor data to per-stock Parquet cache, merging with existing data."""
    if df is None or len(df) == 0:
        return
    _FACTOR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _factor_cache_path(stock_code)
    try:
        if path.exists():
            existing = pd.read_parquet(path)
            existing["trade_date"] = pd.to_datetime(existing["trade_date"])
            # Merge: new data takes precedence
            df = pd.concat([existing, df]).drop_duplicates("trade_date", keep="last").sort_values("trade_date")
        df.to_parquet(path, index=False)
    except Exception as e:
        logger.warning(f"Factor cache save failed for {stock_code}: {e}")


def _fetch_factors_rq(
    stock_codes: list[str],
    start_date: str,
    end_date: str,
    rq_factors: list[str] | None = None,
) -> pd.DataFrame | None:
    """Fetch daily factor data from rqdatac for multiple stocks. Returns DataFrame with stock_code, trade_date, + factor columns."""
    from .market_data import _from_rq_code, _rqdatac_init, _to_rq_code

    if not _rqdatac_init():
        return None

    import rqdatac

    rq_factors = rq_factors or ALL_RQ_FACTORS
    rq_codes = [_to_rq_code(c) for c in stock_codes]

    try:
        raw = rqdatac.get_factor(
            order_book_ids=rq_codes,
            factor=rq_factors,
            start_date=start_date,
            end_date=end_date,
        )
        if raw is None or len(raw) == 0:
            return None
    except Exception as e:
        logger.warning(f"rqdatac get_factor failed: {e}")
        return None

    df = raw.reset_index()
    df["stock_code"] = df["order_book_id"].apply(_from_rq_code)
    df["trade_date"] = pd.to_datetime(df["date"])

    # Rename rqdatac columns → project variable names (handle one-to-many)
    for rq_name, var_names in _RQ_TO_VARS.items():
        if rq_name in df.columns:
            # First var gets the rename, extras get a copy
            df = df.rename(columns={rq_name: var_names[0]})
            for extra_var in var_names[1:]:
                df[extra_var] = df[var_names[0]]

    # Keep only stock_code, trade_date, + variable columns
    var_cols = [c for c in df.columns if c not in ("order_book_id", "date")]
    return df[var_cols]


def prewarm_factors_rq(
    stock_codes: list[str],
    start_date: str = "2015-01-01",
    end_date: str = "2025-12-31",
    batch_size: int = 200,
):
    """Pre-warm factor cache: fetch all factors from rqdatac and save per-stock Parquet files.

    Called by scripts/prewarm.py. Skips stocks already cached for the full date range.
    """
    from .market_data import CACHE_ONLY

    if CACHE_ONLY:
        logger.warning("Cache-only mode: skipping factor prewarm")
        return

    _FACTOR_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Filter out already-cached stocks
    to_fetch = []
    for code in stock_codes:
        cached = _load_factor_cache(code, start_date, end_date)
        if cached is not None and len(cached) > 10:
            continue
        to_fetch.append(code)

    if not to_fetch:
        logger.info(f"Factor prewarm: all {len(stock_codes)} stocks already cached")
        return

    logger.info(f"Factor prewarm: {len(to_fetch)} stocks to fetch ({len(stock_codes) - len(to_fetch)} cached)")

    for i in range(0, len(to_fetch), batch_size):
        batch = to_fetch[i:i + batch_size]
        df = _fetch_factors_rq(batch, start_date, end_date)
        if df is not None and len(df) > 0:
            for code, group in df.groupby("stock_code"):
                _save_factor_cache(code, group)
            logger.info(f"Factor batch {i // batch_size + 1}: {df['stock_code'].nunique()}/{len(batch)} stocks ({i + len(batch)}/{len(to_fetch)} total)")
        else:
            logger.warning(f"Factor batch {i // batch_size + 1}: no data returned")


def enrich_with_fundamentals_rq(
    market_df: pd.DataFrame,
    needed_vars: set[str],
    stock_codes: list[str],
    start_date: str,
    end_date: str,
) -> pd.DataFrame | None:
    """Enrich market_df with fundamental data: local cache → rqdatac → None.

    Returns enriched market_df with fundamental columns added, or None if unavailable.
    """
    from .market_data import _rqdatac_init

    # Determine which rqdatac factors we need
    rq_factors = []
    var_to_rq = {}
    for var in needed_vars:
        rq_name = _RQ_FACTOR_MAP.get(var)
        if rq_name:
            if rq_name not in rq_factors:
                rq_factors.append(rq_name)
            var_to_rq[var] = rq_name  # keep ALL vars, even if rq_name is duplicated

    if not rq_factors:
        return None

    # Step 1: Try loading from per-stock Parquet cache
    cached_parts = []
    uncached_codes = []
    for code in stock_codes:
        cached = _load_factor_cache(code, start_date, end_date)
        if cached is not None:
            # Check if cache has the needed variable columns
            needed_cols = set(var_to_rq.keys()) & set(cached.columns)
            if needed_cols:
                cached_parts.append(cached)
                continue
        uncached_codes.append(code)

    # Step 2: Fetch uncached stocks from rqdatac
    if uncached_codes:
        if _rqdatac_init():
            for i in range(0, len(uncached_codes), 200):
                batch = uncached_codes[i:i + 200]
                fetched = _fetch_factors_rq(batch, start_date, end_date, rq_factors)
                if fetched is not None and len(fetched) > 0:
                    for code, group in fetched.groupby("stock_code"):
                        _save_factor_cache(code, group)
                    cached_parts.append(fetched)
                    logger.info(f"[rqdatac] Fetched factors for {fetched['stock_code'].nunique()} stocks, cached")
        else:
            logger.warning("rqdatac unavailable and factor cache miss, fundamental data will be incomplete")

    if not cached_parts:
        return None

    factor_df = pd.concat(cached_parts, ignore_index=True)

    # Rename any remaining rqdatac column names → project variable names
    rename_map = {rq_name: var_name for var_name, rq_name in var_to_rq.items() if rq_name in factor_df.columns}
    factor_df = factor_df.rename(columns=rename_map)

    # Fill alias columns: if a needed var shares the same rqdatac source as an existing column, copy it
    for var in needed_vars:
        if var not in factor_df.columns:
            rq_name = _RQ_FACTOR_MAP.get(var)
            if rq_name:
                # Find a sibling var that maps to the same rqdatac factor and already exists
                for sibling_var in _RQ_TO_VARS.get(rq_name, []):
                    if sibling_var in factor_df.columns:
                        factor_df[var] = factor_df[sibling_var]
                        break

    # Select only the columns we need
    keep_cols = ["stock_code", "trade_date"] + [v for v in var_to_rq if v in factor_df.columns]
    factor_df = factor_df[keep_cols]

    # Compute derived variables
    if "roa" in needed_vars and "roa" not in factor_df.columns:
        if "roe" in factor_df.columns and "equity_multiplier" in factor_df.columns:
            with np.errstate(divide="ignore", invalid="ignore"):
                factor_df["roa"] = np.where(
                    factor_df["equity_multiplier"] != 0,
                    factor_df["roe"] / factor_df["equity_multiplier"],
                    np.nan,
                )
    if "bps" in needed_vars and "bps" not in factor_df.columns:
        if "net_profit" in factor_df.columns and "roe" in factor_df.columns and "total_share" in factor_df.columns:
            with np.errstate(divide="ignore", invalid="ignore"):
                book = np.where(factor_df["roe"] != 0, factor_df["net_profit"] / factor_df["roe"], np.nan)
                factor_df["bps"] = np.where(factor_df["total_share"] != 0, book / factor_df["total_share"], np.nan)
    if "nav" in needed_vars and "nav" not in factor_df.columns:
        if "net_profit" in factor_df.columns and "roe" in factor_df.columns:
            with np.errstate(divide="ignore", invalid="ignore"):
                factor_df["nav"] = np.where(factor_df["roe"] != 0, factor_df["net_profit"] / factor_df["roe"], np.nan)

    # Merge into market_df
    market_df = market_df.copy()
    merge_cols = [c for c in factor_df.columns if c not in ("stock_code", "trade_date")]
    for col in merge_cols:
        if col in market_df.columns:
            market_df = market_df.drop(columns=[col])

    merged = market_df.merge(
        factor_df,
        on=["stock_code", "trade_date"],
        how="left",
    )

    logger.info(f"[rqdatac] Enriched market_df with {len(merge_cols)} fundamental factors ({len(cached_parts)} sources)")
    return merged


def enrich_market_data(
    market_df: pd.DataFrame,
    fund_vars: set[str],
    stock_codes: list,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    """Enrich market_df with fundamental data if fund_vars is non-empty.

    Uses rqdatac (daily frequency) as primary source, baostock quarterly as fallback.
    """
    if not fund_vars:
        return market_df

    rq_result = enrich_with_fundamentals_rq(market_df, fund_vars, stock_codes, start_date, end_date)
    if rq_result is not None:
        return rq_result

    fetcher = FundamentalDataFetcher()
    non_div_vars = fund_vars - {"dividend_yield"}
    if non_div_vars:
        qdf = fetcher.fetch_fundamentals(stock_codes, start_date, end_date, non_div_vars)
        if qdf is not None and len(qdf) > 0:
            market_df = fetcher.align_to_daily(qdf, market_df, non_div_vars)
    if "dividend_yield" in fund_vars:
        div_df = fetcher.fetch_dividend_data(stock_codes, start_date, end_date)
        if div_df is not None and len(div_df) > 0:
            market_df = fetcher.align_dividends_to_daily(div_df, market_df)
    return market_df
