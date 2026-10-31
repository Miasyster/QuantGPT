"""Celery application for distributed task execution.

Start worker:
    celery -A quantgpt.celery_app worker --loglevel=info --concurrency=4

Requires: pip install 'quantgpt[celery]'
Configure via env:
    CELERY_BROKER_URL       (default: redis://localhost:6379/0)
    CELERY_RESULT_BACKEND   (default: redis://localhost:6379/0)
"""

from __future__ import annotations

import importlib
import logging
import math
import os
import tempfile
import uuid
from pathlib import Path

from celery import Celery

logger = logging.getLogger(__name__)

broker = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
backend = os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")

celery_app = Celery(
    "quantgpt",
    broker=broker,
    backend=backend,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    task_time_limit=900,
    task_soft_time_limit=600,
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=50,
)

ALLOWED_TASKS = {
    "quantgpt.task_executor._run_backtest_in_process",
    "quantgpt.task_executor._run_backtest_precomputed_in_process",
}

CELERY_DATA_DIR = Path(
    os.environ.get("QUANTGPT_CELERY_DATA_DIR", os.path.join(tempfile.gettempdir(), "quantgpt_celery_data"))
)

_PARQUET_MARKER = "__quantgpt_parquet__"


def to_json_transport(obj):
    """Recursively convert obj to JSON-safe form, writing DataFrames to temp Parquet."""
    import numpy as np
    import pandas as pd

    if isinstance(obj, pd.DataFrame):
        return _save_df(obj, "dataframe")
    if isinstance(obj, pd.Series):
        return _save_df(obj.to_frame("__series__"), "series")
    if isinstance(obj, dict):
        return {str(k): to_json_transport(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_json_transport(v) for v in obj]
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        v = float(obj)
        return None if (math.isnan(v) or math.isinf(v)) else v
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.ndarray):
        return to_json_transport(obj.tolist())
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, set):
        return list(obj)
    return obj


def from_json_transport(obj):
    """Recursively restore DataFrames/Series from temp Parquet files."""
    if isinstance(obj, dict):
        if _PARQUET_MARKER in obj:
            return _load_df(obj)
        return {k: from_json_transport(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [from_json_transport(v) for v in obj]
    return obj


def _save_df(df, type_tag: str) -> dict:

    CELERY_DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = CELERY_DATA_DIR / f"{uuid.uuid4().hex}.parquet"
    df.to_parquet(path)
    return {_PARQUET_MARKER: str(path), "__type__": type_tag}


def _load_df(marker: dict):
    import pandas as pd

    path = Path(marker[_PARQUET_MARKER]).resolve()
    allowed = CELERY_DATA_DIR.resolve()
    if not str(path).startswith(str(allowed) + os.sep) and path != allowed:
        raise ValueError("Parquet path outside allowed directory")
    try:
        df = pd.read_parquet(path)
    finally:
        path.unlink(missing_ok=True)
    if marker.get("__type__") == "series":
        return df["__series__"]
    return df


@celery_app.task(name="quantgpt.run_cpu_work", bind=True)
def run_cpu_work(self, fn_path: str, args: list, kwargs: dict):
    """Dispatch CPU work by allowlisted function path."""
    if fn_path not in ALLOWED_TASKS:
        raise ValueError(f"Blocked task function: {fn_path}")

    args = from_json_transport(args)
    kwargs = from_json_transport(kwargs)

    module_path, fn_name = fn_path.rsplit(".", 1)
    mod = importlib.import_module(module_path)
    fn = getattr(mod, fn_name)
    result = fn(*tuple(args), **kwargs)

    return to_json_transport(result)
