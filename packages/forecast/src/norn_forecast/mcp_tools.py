"""
packages/forecast/src/norn_forecast/mcp_tools.py

Логика инструментов MCP-слоя (агентский интерфейс). Чистые функции: на вход
ClickHouse-клиент, на выход JSON-совместимые dict'ы. Читают контракт-таблицы
forecast_point / forecast_segment; протокол MCP добавляется в mcp_server.py.

Методы:
- get_forecast(client, metric, segment, horizon=None) -> list[dict]
- get_expected_range(client, metric, segment, horizon=None) -> list[dict]
- check_ladder_rungs(client, metric, segment, rungs, horizon=None) -> list[dict]
- get_divergence(client, metric, segment, current_value) -> dict
- get_calibration(client, metric, segment) -> dict
"""
from __future__ import annotations

from clickhouse_connect.driver.client import Client


def _latest_run_id(client: Client, metric: str, segment: str) -> str | None:
    rows = client.query(
        "SELECT forecast_run_id FROM forecast_point "
        "WHERE metric_name=%(m)s AND segment_key=%(s)s "
        "ORDER BY created_at DESC LIMIT 1",
        parameters={"m": metric, "s": segment},
    ).result_rows
    return rows[0][0] if rows else None


def get_forecast(
    client: Client, metric: str, segment: str, horizon: int | None = None
) -> list[dict]:
    run_id = _latest_run_id(client, metric, segment)
    if run_id is None:
        return []
    q = (
        "SELECT forecast_ts, horizon_step, y_hat, p10, p50, p90 FROM forecast_point "
        "WHERE forecast_run_id=%(r)s AND metric_name=%(m)s AND segment_key=%(s)s "
    )
    params: dict = {"r": run_id, "m": metric, "s": segment}
    if horizon is not None:
        q += "AND horizon_step <= %(h)s "
        params["h"] = horizon
    q += "ORDER BY horizon_step"
    rows = client.query(q, parameters=params).result_rows
    return [
        {
            "ts": r[0].isoformat(),
            "horizon_step": r[1],
            "y_hat": r[2],
            "p10": r[3],
            "p50": r[4],
            "p90": r[5],
        }
        for r in rows
    ]


def get_expected_range(
    client: Client, metric: str, segment: str, horizon: int | None = None
) -> list[dict]:
    return [
        {
            "ts": p["ts"],
            "horizon_step": p["horizon_step"],
            "low": p["p10"],
            "high": p["p90"],
            "width": p["p90"] - p["p10"],
        }
        for p in get_forecast(client, metric, segment, horizon)
    ]
