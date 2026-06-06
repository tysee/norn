"""
packages/forecast/src/norn_forecast/mcp_tools.py

Logic for the MCP-layer tools of the norn platform (agent interface) without the
protocol itself. Pure "request -> response" functions: a ClickHouse client and
parameters in, JSON-compatible dicts out. They read the forecast_* contract
tables and the dependency tables, always from the freshest run. The FastMCP
wrapper that registers these functions as network tools lives in mcp_server.py.

Methods:
- get_forecast(client, metric, segment, horizon=None) -> list[dict] — points of
  the latest forecast (y_hat + p10/p50/p90).
- get_expected_range(client, metric, segment, horizon=None) -> list[dict] —
  expected p10..p90 range and its width per step.
- classify_levels_vs_band(client, metric, segment, levels, horizon=None) ->
  list[dict] — where the given levels sit relative to the range (below/in/above).
- get_band_position(client, metric, segment, current_value) -> dict — whether the
  current value falls inside the nearest-horizon range.
- get_calibration(client, metric, segment) -> dict — latest quality metrics
  (coverage/wape/mape/bias) from forecast_segment, plus the is_sparse:bool flag
  (a sparse series — calibration is unreliable, treat the intervals with caution).
- get_run_status(client) -> dict — status/metadata of the latest forecast run as a
  whole (forecast_run): model, timings, segments_total/skipped, error. Global, by
  started_at DESC; empty table -> {available:false}.
- get_forecast_status(client, metric, segment) -> dict — freshness+status of the
  forecast for a SPECIFIC series: last point (last_created_at/last_forecast_ts)
  and the metadata of its run (status/model/timings/error); no points ->
  {available:false}.
- list_metrics(client) -> list[str] — discovery: available metrics (DISTINCT
  metric_name from forecast_point, sorted).
- list_segments(client, metric) -> list[str] — discovery: segments with a forecast
  for the metric (DISTINCT segment_key from forecast_point, sorted).
- get_dependencies(client, target_segment, metric) -> list[dict] — lead/lag
  dependencies on the target segment: numeric methods + the agent's verdict.
  Anchored on metric_dependency (always written); the LLM verdict is mixed in via
  a LEFT join, flag explained:bool. On LLM degradation (no explanation) the numeric
  methods are still visible, while is_real=None / explanation="".
- get_dependency_history(client, target_segment, source_segment, metric, limit=20)
  -> list[dict] — history of a single dependency (evidence + decision per run).
Internal helpers:
- _latest_run_id(client, metric, segment) -> str | None — id of the freshest forecast.
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


def get_run_status(client) -> dict:
    """Latest forecast run as a whole (forecast_run), global."""
    rows = client.query(
        "SELECT forecast_run_id, forecast_job, status, model_name, model_version, "
        "started_at, finished_at, segments_total, segments_skipped, error "
        "FROM forecast_run ORDER BY started_at DESC LIMIT 1"
    ).result_rows
    if not rows:
        return {"available": False}
    r = rows[0]
    return {
        "available": True, "forecast_run_id": r[0], "forecast_job": r[1], "status": r[2],
        "model_name": r[3], "model_version": r[4],
        "started_at": r[5].isoformat() if r[5] else None,
        "finished_at": r[6].isoformat() if r[6] else None,
        "segments_total": r[7], "segments_skipped": r[8], "error": r[9],
    }


def get_forecast_status(client, metric: str, segment: str) -> dict:
    """Freshness+status of a specific series' forecast: last point -> its run."""
    run_id = _latest_run_id(client, metric, segment)
    if run_id is None:
        return {"available": False}
    pt = client.query(
        "SELECT max(created_at), max(forecast_ts) FROM forecast_point "
        "WHERE forecast_run_id=%(r)s AND metric_name=%(m)s AND segment_key=%(s)s",
        parameters={"r": run_id, "m": metric, "s": segment},
    ).result_rows[0]
    run = client.query(
        "SELECT status, model_name, model_version, started_at, finished_at, error "
        "FROM forecast_run WHERE forecast_run_id=%(r)s LIMIT 1",
        parameters={"r": run_id},
    ).result_rows
    rr = run[0] if run else (None, None, None, None, None, None)
    return {
        "available": True, "forecast_run_id": run_id,
        "status": rr[0], "model_name": rr[1], "model_version": rr[2],
        "started_at": rr[3].isoformat() if rr[3] else None,
        "finished_at": rr[4].isoformat() if rr[4] else None,
        "error": rr[5],
        "last_created_at": pt[0].isoformat() if pt[0] else None,
        "last_forecast_ts": pt[1].isoformat() if pt[1] else None,
    }


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


def classify_levels_vs_band(
    client: Client,
    metric: str,
    segment: str,
    levels: list[float],
    horizon: int | None = None,
) -> list[dict]:
    pts = get_forecast(client, metric, segment, horizon)
    if not pts:
        return [{"level": x, "verdict": "no_forecast"} for x in levels]
    band_low = min(p["p10"] for p in pts)
    band_high = max(p["p90"] for p in pts)
    out: list[dict] = []
    for x in levels:
        if x < band_low:
            verdict = "below_band"
        elif x > band_high:
            verdict = "above_band"
        else:
            verdict = "in_band"
        out.append(
            {"level": x, "verdict": verdict, "band_low": band_low, "band_high": band_high}
        )
    return out


def get_band_position(
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
        "SELECT coverage, wape, mape, bias, n_points, is_sparse FROM forecast_segment "
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
        "is_sparse": bool(c[5]),
    }


def list_metrics(client) -> list[str]:
    """Metrics available for forecasting (DISTINCT from forecast_point)."""
    rows = client.query(
        "SELECT DISTINCT metric_name FROM forecast_point ORDER BY metric_name"
    ).result_rows
    return [r[0] for r in rows]


def list_segments(client, metric: str) -> list[str]:
    """Segments with a forecast for the metric (DISTINCT from forecast_point)."""
    rows = client.query(
        "SELECT DISTINCT segment_key FROM forecast_point WHERE metric_name=%(m)s "
        "ORDER BY segment_key",
        parameters={"m": metric},
    ).result_rows
    return [r[0] for r in rows]


def get_dependencies(client, target_segment: str, metric: str) -> list[dict]:
    # --- anchor on metric_dependency (always written), not on the LLM explanation ---
    run = client.query(
        "SELECT analysis_run_id FROM metric_dependency "
        "WHERE target_segment=%(t)s AND metric_name=%(m)s "
        "ORDER BY created_at DESC LIMIT 1",
        parameters={"t": target_segment, "m": metric},
    ).result_rows
    if not run:
        return []
    run_id = run[0][0]
    sources = client.query(
        "SELECT DISTINCT source_segment FROM metric_dependency WHERE analysis_run_id=%(r)s",
        parameters={"r": run_id},
    ).result_rows
    out = []
    for (source,) in sources:
        methods = client.query(
            "SELECT method, lag, score, p_value, direction FROM metric_dependency "
            "WHERE analysis_run_id=%(r)s AND source_segment=%(s)s",
            parameters={"r": run_id, "s": source},
        ).result_rows
        exp = client.query(
            "SELECT lag, direction, is_real, confidence, explanation, caveats, change_note "
            "FROM dependency_explanation WHERE analysis_run_id=%(r)s AND source_segment=%(s)s LIMIT 1",
            parameters={"r": run_id, "s": source},
        ).result_rows
        rec = {
            "source_segment": source, "target_segment": target_segment,
            "explained": bool(exp),
            "methods": [
                {"method": m[0], "lag": m[1], "score": m[2], "p_value": m[3], "direction": m[4]}
                for m in methods
            ],
        }
        if exp:
            e = exp[0]
            rec.update({
                "lag": e[0], "direction": e[1], "is_real": bool(e[2]), "confidence": e[3],
                "explanation": e[4], "caveats": e[5], "change_note": e[6],
            })
        else:
            rec.update({
                "lag": None, "direction": None, "is_real": None, "confidence": None,
                "explanation": "", "caveats": "", "change_note": "",
            })
        out.append(rec)
    return out


def get_dependency_history(
    client, target_segment: str, source_segment: str, metric: str, limit: int = 20
) -> list[dict]:
    """Chronological log of a dependency: each past run's evidence + the agent's decision."""
    # --- last N runs for the source->target pair (newest first) ---
    runs = client.query(
        "SELECT analysis_run_id, is_real, confidence, lag, direction, change_note, created_at "
        "FROM dependency_explanation "
        "WHERE target_segment=%(t)s AND source_segment=%(s)s AND metric_name=%(m)s "
        f"ORDER BY created_at DESC LIMIT {int(limit)}",
        parameters={"t": target_segment, "s": source_segment, "m": metric},
    ).result_rows

    # --- per run: the agent's decision + numeric methods from the same epoch ---
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
