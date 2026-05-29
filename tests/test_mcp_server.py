import asyncio

from norn_forecast.mcp_server import TOOL_NAMES, build_server


def test_server_registers_all_tools():
    server = build_server(client=object())  # client unused at registration time
    tools = asyncio.run(server.list_tools())
    assert {t.name for t in tools} == set(TOOL_NAMES)
