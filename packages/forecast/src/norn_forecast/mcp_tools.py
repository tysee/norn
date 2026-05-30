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


def check_ladder_rungs(
    client: Client,
    metric: str,
    segment: str,
    rungs: list[float],
    horizon: int | None = None,
) -> list[dict]:
    pts = get_forecast(client, metric, segment, horizon)
    if not pts:
        return [{"rung": r, "verdict": "no_forecast"} for r in rungs]
    band_low = min(p["p10"] for p in pts)
    band_high = max(p["p90"] for p in pts)
    out: list[dict] = []
    for r in rungs:
        if r < band_low:
            verdict = "below_band"
        elif r > band_high:
            verdict = "above_band"
        else:
            verdict = "in_band"
        out.append(
            {"rung": r, "verdict": verdict, "band_low": band_low, "band_high": band_high}
        )
    return out


def get_divergence(
    client: Client, metric: str, segment: str, current_value: float
) -> dict:
    pts = get_forecast(client, metric, segment, horizon=1)
    if not pts:
        return {"in_band": None, "position": "no_forecast"}
    p = pts[0]
    if current_value < p["p10"]:
        position = "below_p10"
    elif current_value > p["p90"]:
        position = "above_p90"
    else:
        position = "in_band"
    return {
        "in_band": position == "in_band",
        "position": position,
        "p10": p["p10"],
        "p90": p["p90"],
        "current": current_value,
    }


def get_calibration(client: Client, metric: str, segment: str) -> dict:
    rows = client.query(
        "SELECT coverage, wape, mape, bias, n_points FROM forecast_segment "
        "WHERE metric_name=%(m)s AND segment_key=%(s)s ORDER BY created_at DESC LIMIT 1",
        parameters={"m": metric, "s": segment},
    ).result_rows
    if not rows:
        return {"available": False}
    c = rows[0]
    return {
        "available": True,
        "coverage": c[0],
        "wape": c[1],
        "mape": c[2],
        "bias": c[3],
        "n_points": c[4],
    }


def get_dependencies(client, target_segment: str, metric: str) -> list[dict]:
    run = client.query(
        "SELECT analysis_run_id FROM dependency_explanation "
        "WHERE target_segment=%(t)s AND metric_name=%(m)s "
        "ORDER BY created_at DESC LIMIT 1",
        parameters={"t": target_segment, "m": metric},
    ).result_rows
    if not run:
        return []
    run_id = run[0][0]
    rels = client.query(
        "SELECT source_segment, lag, direction, is_real, confidence, explanation, "
        "caveats, change_note FROM dependency_explanation WHERE analysis_run_id=%(r)s",
        parameters={"r": run_id},
    ).result_rows
    out = []
    for r in rels:
        source = r[0]
        methods = client.query(
            "SELECT method, lag, score, p_value, direction FROM metric_dependency "
            "WHERE analysis_run_id=%(r)s AND source_segment=%(s)s",
            parameters={"r": run_id, "s": source},
        ).result_rows
        out.append({
            "source_segment": source,
            "target_segment": target_segment,
            "lag": r[1],
            "direction": r[2],
            "is_real": bool(r[3]),
            "confidence": r[4],
            "explanation": r[5],
            "caveats": r[6],
            "change_note": r[7],
            "methods": [
                {"method": m[0], "lag": m[1], "score": m[2], "p_value": m[3], "direction": m[4]}
                for m in methods
            ],
        })
    return out


def get_dependency_history(
    client, target_segment: str, source_segment: str, metric: str, limit: int = 20
) -> list[dict]:
    """Chronological log of a dependency: each past run's evidence + the agent's decision."""
    runs = client.query(
        "SELECT analysis_run_id, is_real, confidence, lag, direction, change_note, created_at "
        "FROM dependency_explanation "
        "WHERE target_segment=%(t)s AND source_segment=%(s)s AND metric_name=%(m)s "
        f"ORDER BY created_at DESC LIMIT {int(limit)}",
        parameters={"t": target_segment, "s": source_segment, "m": metric},
    ).result_rows
    history = []
    for run in runs:
        run_id = run[0]
        methods = client.query(
            "SELECT method, lag, score, p_value FROM metric_dependency "
            "WHERE analysis_run_id=%(r)s AND source_segment=%(s)s",
            parameters={"r": run_id, "s": source_segment},
        ).result_rows
        history.append({
            "analysis_run_id": run_id,
            "created_at": run[6].isoformat(),
            "is_real": bool(run[1]),
            "confidence": run[2],
            "lag": run[3],
            "direction": run[4],
            "change_note": run[5],
            "methods": [
                {"method": m[0], "lag": m[1], "score": m[2], "p_value": m[3]} for m in methods
            ],
        })
    return history
