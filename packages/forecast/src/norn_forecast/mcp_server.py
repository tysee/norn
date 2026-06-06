"""
packages/forecast/src/norn_forecast/mcp_server.py

MCP server of the norn platform (agent interface) on top of the ClickHouse
contract. A thin FastMCP wrapper over the pure mcp_tools functions: it registers
them as network tools, passing through a shared ClickHouse client and leaving all
logic in mcp_tools. This is how an agent (on the Pi or remote) obtains forecasts,
ranges, calibration and dependencies over the network. Host/port come from the
mcp config.

Methods:
- build_server(client=None) -> FastMCP — assembles the server and registers the
  tools from TOOL_NAMES (default client — get_client()).
- TOOL_NAMES — names of the registered tools (11).
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from norn_core.clickhouse import get_client
from norn_forecast import mcp_tools

TOOL_NAMES = [
    "get_forecast",
    "get_expected_range",
    "classify_levels_vs_band",
    "get_band_position",
    "get_calibration",
    "get_dependencies",
    "get_dependency_history",
    "get_run_status",
    "get_forecast_status",
    "list_metrics",
    "list_segments",
]


def build_server(client=None) -> FastMCP:
    client = client if client is not None else get_client()
    from norn_core.config import get_settings

    mcp_cfg = get_settings().mcp
    mcp = FastMCP("norn", host=mcp_cfg.host, port=mcp_cfg.port)

    @mcp.tool()
    def get_forecast(metric: str, segment: str, horizon: int | None = None) -> list[dict]:
        """Latest forecast points (y_hat + p10/p50/p90) for a metric/segment."""
        return mcp_tools.get_forecast(client, metric, segment, horizon)

    @mcp.tool()
    def get_expected_range(metric: str, segment: str, horizon: int | None = None) -> list[dict]:
        """Expected range (p10..p90 and width) per horizon step."""
        return mcp_tools.get_expected_range(client, metric, segment, horizon)

    @mcp.tool()
    def classify_levels_vs_band(
        metric: str, segment: str, levels: list[float], horizon: int | None = None
    ) -> list[dict]:
        """Classify each value vs the forecast band (below/in/above). Consumers (e.g. a trading bot) pass their own levels."""
        return mcp_tools.classify_levels_vs_band(client, metric, segment, levels, horizon)

    @mcp.tool()
    def get_band_position(metric: str, segment: str, current_value: float) -> dict:
        """Whether a current value sits inside the nearest-horizon forecast band."""
        return mcp_tools.get_band_position(client, metric, segment, current_value)

    @mcp.tool()
    def get_calibration(metric: str, segment: str) -> dict:
        """Latest calibration metrics (coverage/wape/mape/bias) for a metric/segment."""
        return mcp_tools.get_calibration(client, metric, segment)

    @mcp.tool()
    def get_dependencies(target_segment: str, metric: str) -> list[dict]:
        """Lead/lag dependencies pointing at a target segment: numeric evidence + the agent's judgment."""
        return mcp_tools.get_dependencies(client, target_segment, metric)

    @mcp.tool()
    def get_dependency_history(
        target_segment: str, source_segment: str, metric: str, limit: int = 20
    ) -> list[dict]:
        """Chronological log of one dependency (evidence + decision per run) to compare drift over time."""
        return mcp_tools.get_dependency_history(client, target_segment, source_segment, metric, limit)

    @mcp.tool()
    def get_run_status() -> dict:
        """Status/metadata of the latest forecast run (model, timings, segments, error)."""
        return mcp_tools.get_run_status(client)

    @mcp.tool()
    def get_forecast_status(metric: str, segment: str) -> dict:
        """Freshness + run status for a specific metric/segment forecast."""
        return mcp_tools.get_forecast_status(client, metric, segment)

    @mcp.tool()
    def list_metrics() -> list[str]:
        """List metric names that have forecasts."""
        return mcp_tools.list_metrics(client)

    @mcp.tool()
    def list_segments(metric: str) -> list[str]:
        """List segment keys that have forecasts for a metric."""
        return mcp_tools.list_segments(client, metric)

    return mcp
