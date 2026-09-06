"""Dedicated lightweight Streamable HTTP entry point for OpenAI Codex.

Start on Windows with::

    python -m quantgpt.codex_mcp_server --host 127.0.0.1 --port 8003

Codex should connect to ``http://127.0.0.1:8003/mcp``.
"""

import argparse
import logging
import sys

from .mcp_transport import run_streamable_http


def main() -> None:
    parser = argparse.ArgumentParser(description="QuantGPT MCP server for OpenAI Codex")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8003)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    from .mcp_server import mcp

    run_streamable_http(mcp, host=args.host, port=args.port, path="/mcp")


if __name__ == "__main__":
    main()
