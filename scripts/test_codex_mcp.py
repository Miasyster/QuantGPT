"""Minimal end-to-end check for the Codex Streamable HTTP MCP endpoint."""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client


async def _call_score(read_stream, write_stream, timeout: int) -> dict:
    arguments = {
        "expression": "rank(close / ts_shift(close, 20) - 1)",
        "universe": "small_scale",
        "start_date": "2024-01-01",
        "end_date": "2025-12-31",
    }
    async with ClientSession(read_stream, write_stream) as session:
        await session.initialize()
        result = await session.call_tool(
            "score_factor",
            arguments,
            read_timeout_seconds=timedelta(seconds=timeout),
        )
    if result.isError:
        raise RuntimeError(f"score_factor returned an MCP error: {result.content}")

    text_blocks = [block.text for block in result.content if block.type == "text"]
    if not text_blocks:
        raise RuntimeError(f"score_factor returned no text content: {result.content}")
    payload = json.loads(text_blocks[0])
    if "error" in payload:
        raise RuntimeError(f"score_factor failed: {payload['error']}")
    return payload


async def run(url: str, timeout: int, transport: str) -> None:
    started = time.perf_counter()
    if transport == "stdio":
        env = dict(os.environ)
        env.setdefault("QUANTGPT_TASK_BACKEND", "thread")
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "quantgpt"],
            cwd=Path(__file__).resolve().parent.parent,
            env=env,
        )
        async with stdio_client(params) as (read_stream, write_stream):
            payload = await _call_score(read_stream, write_stream, timeout)
    else:
        async with streamable_http_client(url) as (read_stream, write_stream, _):
            payload = await _call_score(read_stream, write_stream, timeout)

    elapsed = time.perf_counter() - started
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"score_factor completed through {transport} MCP in {elapsed:.2f}s")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=["http", "stdio"], default="http")
    parser.add_argument("--url", default="http://127.0.0.1:8003/mcp")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    asyncio.run(run(args.url, args.timeout, args.transport))


if __name__ == "__main__":
    main()
