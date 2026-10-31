"""Factor values computation endpoint — cross-sectional factor scores.

Computes raw factor values for all stocks in a universe on each trading day.
Output format: [{date, values: {symbol: score}}] — suitable for downstream
portfolio construction, external analysis, or factor library upload.
"""

import asyncio
import logging
from datetime import date

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from ..auth import get_current_user
from ..backtest import api_context
from ..expression_parser import parse_expression
from ..market_data import MarketDataFetcher, get_universe
from ..models import User
from ..schemas import VALID_UNIVERSES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/factor_values", tags=["factor_values"])

_MAX_DATE_RANGE_DAYS = 750


class FactorValuesRequest(BaseModel):
    expression: str
    universe: str = "csi500"
    start_date: str = ""
    end_date: str = ""

    @field_validator("expression")
    @classmethod
    def validate_expression(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("expression must not be empty")
        if len(v) > 2000:
            raise ValueError("expression too long (max 2000 chars)")
        return v

    @field_validator("universe")
    @classmethod
    def validate_universe(cls, v: str) -> str:
        if v not in VALID_UNIVERSES:
            raise ValueError(f"universe must be one of {VALID_UNIVERSES}")
        return v

    @field_validator("start_date", "end_date")
    @classmethod
    def validate_dates(cls, v: str) -> str:
        if v:
            from datetime import datetime
            try:
                datetime.strptime(v, "%Y-%m-%d")
            except ValueError:
                raise ValueError("date must be YYYY-MM-DD format")
        return v


@router.post("")
async def compute_factor_values(
    req: FactorValuesRequest,
    user: User = Depends(get_current_user),
):
    try:
        factor_fn = parse_expression(req.expression)
    except Exception as e:
        raise HTTPException(400, f"Invalid expression: {e}")

    result = await asyncio.to_thread(
        _compute_values_sync,
        factor_fn,
        req.expression,
        req.universe,
        req.start_date,
        req.end_date,
    )
    return result


def _compute_values_sync(
    factor_fn,
    expression: str,
    universe: str,
    start_date: str,
    end_date: str,
) -> dict:
    with api_context():
        fetcher = MarketDataFetcher()
        stocks = get_universe(universe)
        if not stocks:
            raise HTTPException(400, f"Empty universe: {universe}")

        end_dt = end_date or date.today().isoformat()
        if not start_date:
            from datetime import timedelta
            start_dt = (date.fromisoformat(end_dt) - timedelta(days=365)).isoformat()
        else:
            start_dt = start_date

        from datetime import date as date_type
        d_start = date_type.fromisoformat(start_dt)
        d_end = date_type.fromisoformat(end_dt)
        if (d_end - d_start).days > _MAX_DATE_RANGE_DAYS:
            raise HTTPException(400, f"Date range too large (max {_MAX_DATE_RANGE_DAYS} days)")

        extra_days = 260
        from datetime import timedelta
        fetch_start = (d_start - timedelta(days=extra_days)).isoformat()

        df = fetcher.fetch_stocks(stocks, fetch_start, end_dt)
        if df is None or df.empty:
            raise HTTPException(400, "No market data available for this universe/date range")

        try:
            factor_values = factor_fn(df)
        except Exception as e:
            raise HTTPException(400, f"Expression evaluation failed: {e}")

        df["factor_value"] = factor_values
        result_df = df[df["trade_date"] >= start_dt][["trade_date", "stock_code", "factor_value"]].copy()
        result_df = result_df.dropna(subset=["factor_value"])

        dates_data = []
        for trade_date, group in result_df.groupby("trade_date"):
            values = {}
            for _, row in group.iterrows():
                val = row["factor_value"]
                if np.isfinite(val):
                    values[row["stock_code"]] = round(float(val), 6)
            if values:
                dates_data.append({
                    "date": str(trade_date),
                    "values": values,
                    "count": len(values),
                })

        return {
            "expression": expression,
            "universe": universe,
            "start_date": start_dt,
            "end_date": end_dt,
            "trading_days": len(dates_data),
            "data": dates_data,
        }
