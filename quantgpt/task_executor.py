"""Task execution backends: ProcessPool (default) or Celery (distributed).

Backtest orchestration threads call ``get_executor().submit_cpu_work(fn, ...)``
to offload CPU-bound pandas/numpy work to a separate process, bypassing the GIL.

Configuration via environment variables:
    QUANTGPT_TASK_BACKEND  = "process" | "celery" | "thread"  (default: process)
    QUANTGPT_WORKER_PROCESSES = int  (default: min(4, cpu_count))
    CELERY_BROKER_URL      = redis://...  (only for celery backend)
    CELERY_RESULT_BACKEND  = redis://...  (only for celery backend)
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import os
import sys
from abc import ABC, abstractmethod
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from contextlib import redirect_stdout

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Top-level wrappers — must be picklable for ProcessPoolExecutor
# ---------------------------------------------------------------------------

def _run_backtest_in_process(market_df, expression, n_groups, holding_period, **kwargs):
    from quantgpt.backtest import disable_api_context, enable_api_context, run_factor_backtest
    enable_api_context()
    try:
        # A spawned worker inherits the MCP stdout pipe on Windows.  Prevent
        # third-party print() calls from corrupting the parent's JSON-RPC stream.
        with redirect_stdout(sys.stderr):
            return run_factor_backtest(market_df, expression, n_groups, holding_period, **kwargs)
    finally:
        disable_api_context()


def _run_backtest_precomputed_in_process(market_df, n_groups, holding_period, cost_rate, precomputed_factor):
    from quantgpt.backtest import disable_api_context, enable_api_context, run_factor_backtest
    enable_api_context()
    try:
        with redirect_stdout(sys.stderr):
            return run_factor_backtest(
                market_df, n_groups=n_groups, holding_period=holding_period,
                cost_rate=cost_rate, precomputed_factor=precomputed_factor,
            )
    finally:
        disable_api_context()


# ---------------------------------------------------------------------------
# Abstract executor interface
# ---------------------------------------------------------------------------

class TaskExecutor(ABC):
    is_process_based: bool = False

    @abstractmethod
    def submit_cpu_work(self, fn, *args, **kwargs) -> Future:
        ...

    @abstractmethod
    def shutdown(self) -> None:
        ...


# ---------------------------------------------------------------------------
# ProcessPool executor (default)
# ---------------------------------------------------------------------------

class ProcessPoolTaskExecutor(TaskExecutor):
    is_process_based = True

    def __init__(self):
        cpu = os.cpu_count() or 4
        self._max_workers = int(os.environ.get("QUANTGPT_WORKER_PROCESSES", str(min(4, cpu))))
        ctx = mp.get_context("spawn")
        self._pool = ProcessPoolExecutor(max_workers=self._max_workers, mp_context=ctx)
        logger.info(f"ProcessPoolTaskExecutor initialized with {self._max_workers} workers")

    def submit_cpu_work(self, fn, *args, **kwargs) -> Future:
        return self._pool.submit(fn, *args, **kwargs)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False)
        logger.info("ProcessPoolTaskExecutor shut down")


# ---------------------------------------------------------------------------
# Thread executor (fallback / testing)
# ---------------------------------------------------------------------------

class ThreadTaskExecutor(TaskExecutor):
    is_process_based = False

    def __init__(self):
        self._pool = ThreadPoolExecutor(max_workers=int(os.environ.get("QUANTGPT_WORKER_PROCESSES", "4")))
        logger.info("ThreadTaskExecutor initialized")

    def submit_cpu_work(self, fn, *args, **kwargs) -> Future:
        return self._pool.submit(fn, *args, **kwargs)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False)


# ---------------------------------------------------------------------------
# Celery executor (distributed)
# ---------------------------------------------------------------------------

class CeleryTaskExecutor(TaskExecutor):
    is_process_based = True

    _FN_PATHS = {
        _run_backtest_in_process: "quantgpt.task_executor._run_backtest_in_process",
        _run_backtest_precomputed_in_process: "quantgpt.task_executor._run_backtest_precomputed_in_process",
    }

    def __init__(self):
        from .celery_app import celery_app
        self._app = celery_app
        logger.info("CeleryTaskExecutor initialized")

    def submit_cpu_work(self, fn, *args, **kwargs) -> Future:
        fn_path = self._FN_PATHS.get(fn)
        if fn_path is None:
            raise ValueError(f"Function not registered for Celery dispatch: {fn.__name__}")
        from .celery_app import run_cpu_work, to_json_transport
        ser_args = to_json_transport(list(args))
        ser_kwargs = to_json_transport(kwargs)
        async_result = run_cpu_work.apply_async(args=(fn_path, ser_args, ser_kwargs))
        return _CeleryFutureAdapter(async_result)

    def shutdown(self) -> None:
        pass


class _CeleryFutureAdapter:
    """Adapt Celery AsyncResult to concurrent.futures.Future interface."""

    def __init__(self, async_result):
        self._ar = async_result

    def result(self, timeout=None):
        from .celery_app import from_json_transport
        raw = self._ar.get(timeout=timeout)
        return from_json_transport(raw)

    def cancel(self):
        self._ar.revoke(terminate=True)
        return True

    def done(self):
        return self._ar.ready()


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_executor: TaskExecutor | None = None


def get_executor() -> TaskExecutor:
    global _executor
    if _executor is not None:
        return _executor

    backend = os.environ.get("QUANTGPT_TASK_BACKEND", "process").lower()
    if backend == "celery":
        _executor = CeleryTaskExecutor()
    elif backend == "thread":
        _executor = ThreadTaskExecutor()
    else:
        _executor = ProcessPoolTaskExecutor()
    return _executor


def shutdown_executor() -> None:
    global _executor
    if _executor is not None:
        _executor.shutdown()
        _executor = None
