"""
packages/forecast/src/norn_forecast/mcp_server.py

MCP-сервер (агентский интерфейс) поверх ClickHouse-контракта. Тонкая обёртка
FastMCP над чистыми функциями mcp_tools. Транспорт — streamable-http (Pi/агент
ходит по сети).

Методы:
- build_server(client=None) -> FastMCP — собирает сервер с 5 инструментами.
- TOOL_NAMES — имена зарегистрированных инструментов.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from norn_core.clickhouse import get_client
from norn_forecast import mcp_tools

TOOL_NAMES = [
    "get_forecast",
    "get_expected_range",
    "check_ladder_rungs",
    "get_divergence",
    "get_calibration",
    "get_dependencies",
    "get_dependency_history",
]


def build_server(client=None) -> FastMCP:
    client = client if client is not None else get_client()
    mcp = FastMCP("norn", host="127.0.0.1", port=9200)

    @mcp.tool()
    def get_forecast(metric: str, segment: str, horizon: int | None = None) -> list[dict]:
        """Latest forecast points (y_hat + p10/p50/p90) for a metric/segment."""
        return mcp_tools.get_forecast(client, metric, segment, horizon)

    @mcp.tool()
    def get_expected_range(metric: str, segment: str, horizon: int | None = None) -> list[dict]:
        """Expected range (p10..p90 and width) per horizon step."""
        return mcp_tools.get_expected_range(client, metric, segment, horizon)

    @mcp.tool()
    def check_ladder_rungs(
        metric: str, segment: str, rungs: list[float], horizon: int | None = None
    ) -> list[dict]:
        """Classify each proposed ladder rung price vs the forecast band."""
        return mcp_tools.check_ladder_rungs(client, metric, segment, rungs, horizon)

    @mcp.tool()
    def get_divergence(metric: str, segment: str, current_value: float) -> dict:
        """Whether a current value sits inside the nearest-horizon forecast band."""
        return mcp_tools.get_divergence(client, metric, segment, current_value)

    @mcp.tool()
    def get_calibration(metric: str, segment: str) -> dict:
        """Latest calibration metrics (coverage/wape/mape/bias) for a metric/segment."""
        return mcp_tools.get_calibration(client, metric, segment)

    @mcp.tool()
    def get_dependencies(target_segment: str, metric: str = "log_return") -> list[dict]:
        """Lead/lag dependencies pointing at a target segment: numeric evidence + the agent's judgment."""
        return mcp_tools.get_dependencies(client, target_segment, metric)

    @mcp.tool()
    def get_dependency_history(
        target_segment: str, source_segment: str, metric: str = "log_return", limit: int = 20
    ) -> list[dict]:
        """Chronological log of one dependency (evidence + decision per run) to compare drift over time."""
        return mcp_tools.get_dependency_history(client, target_segment, source_segment, metric, limit)

    return mcp
