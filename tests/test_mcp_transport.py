import io
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from quantgpt import mcp_transport


@pytest.mark.asyncio
async def test_clean_stdio_routes_prints_to_stderr_and_restores_stdout(monkeypatch):
    protocol_stdout = io.StringIO()
    application_stderr = io.StringIO()
    observed = {}

    @asynccontextmanager
    async def fake_stdio_server():
        observed["captured_stdout"] = sys.stdout
        yield object(), object()

    class FakeLowLevelServer:
        def create_initialization_options(self):
            return object()

        async def run(self, read_stream, write_stream, initialization_options):
            print("third-party noise")
            observed["stdout_during_run"] = sys.stdout

    server = SimpleNamespace(_mcp_server=FakeLowLevelServer())
    monkeypatch.setattr(mcp_transport, "stdio_server", fake_stdio_server)
    monkeypatch.setattr(sys, "stdout", protocol_stdout)
    monkeypatch.setattr(sys, "stderr", application_stderr)

    await mcp_transport._run_clean_stdio_async(server)

    assert observed["captured_stdout"] is protocol_stdout
    assert observed["stdout_during_run"] is application_stderr
    assert protocol_stdout.getvalue() == ""
    assert application_stderr.getvalue() == "third-party noise\n"
    assert sys.stdout is protocol_stdout


def test_streamable_http_uses_explicit_path_port_and_loopback_hosts(monkeypatch):
    calls = {}
    security = SimpleNamespace(allowed_hosts=[])
    settings = SimpleNamespace(streamable_http_path="/", transport_security=security)
    app = object()
    server = SimpleNamespace(settings=settings, streamable_http_app=lambda: app)

    def fake_run(asgi_app, *, host, port):
        calls.update(app=asgi_app, host=host, port=port)

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", fake_run)
    mcp_transport.run_streamable_http(server, host="127.0.0.1", port=8123, path="/mcp/")

    assert settings.streamable_http_path == "/mcp"
    assert "localhost:8123" in security.allowed_hosts
    assert "127.0.0.1:8123" in security.allowed_hosts
    assert calls == {"app": app, "host": "127.0.0.1", "port": 8123}
