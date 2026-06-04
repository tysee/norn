import os
import socket
import subprocess
import time
from pathlib import Path

import anyio
from typer.testing import CliRunner

from conftest import DSN
from norn_cli.main import app
from norn_forecast.mcp_server import TOOL_NAMES

runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parents[1]


def _combined_output(result) -> str:
    out = result.output
    try:
        out += result.stderr
    except (ValueError, AttributeError):
        pass  # stderr not captured separately on this click version
    return out


def test_mcp_missing_password_fails_clearly(monkeypatch):
    # Regression: `norn mcp` without NORN_DB_PASSWORD used to dump a raw pydantic
    # ValidationError traceback. It must exit 1 with a message naming the env var.
    monkeypatch.delenv("NORN_DB_PASSWORD", raising=False)
    result = runner.invoke(app, ["mcp"])
    assert result.exit_code == 1
    assert "NORN_DB_PASSWORD" in _combined_output(result)
    assert result.exception is None or not str(type(result.exception)).count("ValidationError")


def test_mcp_server_e2e_streamable_http():
    # Launch the real `norn mcp` server as a subprocess against the TEST DB and
    # talk to it over streamable-http with the MCP client: initialize, list tools,
    # call a read-only tool. This is the launch path that was never exercised.
    port = 9277
    env = {
        **os.environ,
        "NORN_DB_PASSWORD": "norn",
        "NORN_CLICKHOUSE_URL": DSN,  # same DB as the ch fixture, never live
        "NORN_MCP_HOST": "127.0.0.1",
        "NORN_MCP_PORT": str(port),
    }
    proc = subprocess.Popen(
        ["uv", "run", "norn", "mcp"],
        cwd=REPO_ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if proc.poll() is not None:  # died early — surface its output
                raise AssertionError(
                    f"norn mcp exited rc={proc.returncode}\n{proc.communicate()[1]}"
                )
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.3)
        else:
            raise AssertionError("norn mcp never opened its port")

        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async def _roundtrip():
            async with streamablehttp_client(f"http://127.0.0.1:{port}/mcp") as (r, w, _):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    status = await session.call_tool("get_run_status", {})
                    return {t.name for t in tools.tools}, status

        names, status = anyio.run(_roundtrip)
        assert set(TOOL_NAMES) <= names
        assert status.isError is False
        assert status.content  # structured payload (even {"available": false} on empty DB)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
