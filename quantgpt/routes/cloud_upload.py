"""Cloud upload routes — push local factor values to QuantGPT Cloud."""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from ..auth import get_current_user
from ..cloud_client import _VALID_UNIVERSES, CloudAPIError, CloudClient, get_cloud_url, is_configured
from ..models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/cloud", tags=["cloud"])


class CloudUploadRequest(BaseModel):
    expression: str
    universe: str
    name: str | None = None
    claimed_ic_mean: float | None = None
    claimed_ic_ir: float | None = None
    factor_values_data: list[dict]

    @field_validator("universe")
    @classmethod
    def validate_universe(cls, v: str) -> str:
        if v not in _VALID_UNIVERSES:
            raise ValueError(f"Cloud only supports universes: {_VALID_UNIVERSES}")
        return v

    @field_validator("factor_values_data")
    @classmethod
    def validate_data(cls, v: list[dict]) -> list[dict]:
        if not v:
            raise ValueError("factor_values_data must not be empty")
        return v


@router.get("/status")
async def cloud_status():
    return {
        "configured": is_configured(),
        "cloud_url": get_cloud_url(),
    }


@router.post("/upload")
async def upload_to_cloud(
    req: CloudUploadRequest,
    user: User = Depends(get_current_user),
):
    if not is_configured():
        raise HTTPException(400, "QuantGPT Cloud not configured. Set QUANTGPT_CLOUD_EMAIL and QUANTGPT_CLOUD_PASSWORD in .env")

    name = req.name or req.expression[:80]

    try:
        client = CloudClient()
        result = await asyncio.to_thread(
            client.upload_and_validate,
            name=name,
            universe=req.universe,
            factor_values_data=req.factor_values_data,
            expression=req.expression,
            claimed_ic_mean=req.claimed_ic_mean,
            claimed_ic_ir=req.claimed_ic_ir,
        )
        return result
    except CloudAPIError as e:
        raise HTTPException(502, str(e))
    except Exception:
        logger.exception("Cloud upload failed")
        raise HTTPException(502, "Cloud upload failed unexpectedly")
