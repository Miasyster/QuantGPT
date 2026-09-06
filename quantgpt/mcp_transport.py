"""Transport compatibility helpers for MCP clients.

MCP stdio reserves stdout exclusively for JSON-RPC.  Some QuantGPT data
providers (notably baostock) use bare ``print()`` calls, so the normal process
stdout cannot safely remain visible to application code while a stdio server
is running.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import anyio
from mcp.server.stdio import stdio_server

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


async def _run_clean_stdio_async(server: "FastMCP") -> None:
    """Run FastMCP with a protocol-only stdout stream.

    ``stdio_server`` captures the original stdout before it is redirected.
    Application and third-party ``print()`` calls then go to stderr, while the
    captured stream continues to carry and flush JSON-RPC responses.
    """
    async with stdio_server() as (read_stream, write_stream):
        protocol_stdout = sys.stdout
        sys.stdout = sys.stderr
        try:
            await server._mcp_server.run(  # FastMCP exposes no public stream runner
                read_stream,
                write_stream,
                server._mcp_server.create_initialization_options(),
            )
        finally:
            sys.stdout = protocol_stdout


def run_clean_stdio(server: "FastMCP") -> None:
    """Synchronous entry point for MCP stdio with stdout isolation."""
    anyio.run(_run_clean_stdio_async, server)


def run_streamable_http(
    server: "FastMCP",
    *,
    host: str,
    port: int,
    path: str = "/mcp",
) -> None:
    """Run a lightweight Streamable HTTP MCP endpoint at an explicit address."""
    import uvicorn

    normalized_path = "/" + path.strip("/") if path.strip("/") else "/"
    server.settings.streamable_http_path = normalized_path

    # FastMCP's DNS-rebinding protection validates the HTTP Host header.  Keep
    # loopback aliases usable when a non-default port is selected on Windows.
    security = server.settings.transport_security
    if security is not None:
        allowed = set(security.allowed_hosts)
        allowed.update({
            "localhost",
            f"localhost:{port}",
            "127.0.0.1",
            f"127.0.0.1:{port}",
        })
        security.allowed_hosts = sorted(allowed)

    uvicorn.run(server.streamable_http_app(), host=host, port=port)
