# MCP Interface

*Audience: agent integrators — bots, assistants, and services that read norn's forecasts and dependencies over the network.*

norn exposes its forecast and dependency contract tables to agents through an **MCP server**. This page covers how to connect, the full 11-tool reference, abstract call examples, and how to reason about data freshness and graceful degradation.

## What MCP is here

The MCP server is a thin, read-only interface over the forecast/dependency contract tables in ClickHouse. It is a [FastMCP](https://github.com/jlowin/fastmcp) wrapper around plain query functions: every tool runs a single read against the warehouse and returns plain JSON. There is **no write path** — agents consume forecasts, expected ranges, calibration, and lead/lag dependencies; they never produce them. Jobs (`norn forecast`, `norn deps`, `norn calibrate`) populate the tables; the MCP tools read whatever the latest run wrote.

Typical consumers: a trading or alerting bot, an LLM agent answering "is this value inside the expected band?", or a dashboard backend that needs the freshest forecast points.

## Connecting

Start the server with the CLI:

```bash
uv run norn mcp
```

This serves over the **streamable-http** transport at `http://<host>:<port>/mcp` (note the `/mcp` path), binding to `mcp.host` / `mcp.port` from `mcp.yml`. The CLI echoes the exact URL on startup. The defaults are:

| Field | Default | Meaning |
|---|---|---|
| `host` | `127.0.0.1` | Interface to bind. Loopback by default (local only). |
| `port` | `9200` | TCP port. |

The bind host is **config-driven**. The default `127.0.0.1` means the server is reachable only from the same machine. To expose it to remote agents, set `host` to a routable interface (e.g. `0.0.0.0`) in `mcp.yml` or via the env override `NORN_MCP_HOST` — and put it behind your own network controls. See [Configuration](configuration.md) for the override pattern.

### Example MCP client config

Point your agent/client at the streamable-http URL. A generic client entry looks like:

```json
{
  "mcpServers": {
    "norn": {
      "transport": "streamable-http",
      "url": "http://<host>:<port>/mcp"
    }
  }
}
```

With the defaults that URL is `http://127.0.0.1:9200/mcp`. The server name registered by norn is `norn`.

## Tools

There are exactly **11 tools**. Common parameters: `metric` is a metric name (see `list_metrics`); `segment` / `target_segment` / `source_segment` are segment keys of the form `<dim=value>` (see `list_segments`); `horizon` (where present) is optional and limits how many horizon steps are returned (default: all available).

| Tool | Params | Returns | Purpose |
|---|---|---|---|
| `get_forecast` | `metric, segment, horizon=None` | list of `{ts, horizon_step, y_hat, p10, p50, p90}` | Latest forecast points (point estimate + quantile band) for a metric/segment. |
| `get_expected_range` | `metric, segment, horizon=None` | list of `{ts, horizon_step, low, high, width}` | The expected `p10..p90` corridor and its `width` per horizon step. |
| `classify_levels_vs_band` | `metric, segment, levels, horizon=None` | list of `{level, verdict, band_low, band_high}` | Classify caller-supplied `levels` against the forecast band; `verdict` is `below_band` / `in_band` / `above_band`. |
| `get_band_position` | `metric, segment, current_value` | `{in_band, position, p10, p90, current}` | Whether a current value sits inside the nearest-horizon band; `position` is `below_p10` / `in_band` / `above_p90`. |
| `get_calibration` | `metric, segment` | `{available, coverage, wape, mape, bias, n_points, is_sparse}` | Latest rolling-origin calibration metrics for a metric/segment. |
| `get_dependencies` | `target_segment, metric` | list of `{source_segment, target_segment, explained, methods[], lag, direction, is_real, confidence, explanation, caveats, change_note}` | Lead/lag dependencies pointing at a target segment: numeric evidence plus the agent's judgment. |
| `get_dependency_history` | `target_segment, source_segment, metric, limit=20` | list of `{analysis_run_id, created_at, is_real, confidence, lag, direction, change_note, methods[]}` | Chronological log of one dependency (one entry per past run) to see drift over time. |
| `get_run_status` | (none) | `{available, forecast_run_id, forecast_job, status, model_name, model_version, started_at, finished_at, segments_total, segments_skipped, error}` | Status/metadata of the latest forecast run across the platform. |
| `get_forecast_status` | `metric, segment` | `{available, forecast_run_id, status, model_name, model_version, started_at, finished_at, error, last_created_at, last_forecast_ts}` | Freshness + run status for one metric/segment forecast. |
| `list_metrics` | (none) | list of metric names | Discover which metrics have forecasts. |
| `list_segments` | `metric` | list of segment keys | Discover which segments have forecasts for a metric. |

### Notes on nested fields

- **`methods[]`** (in `get_dependencies` / `get_dependency_history`) is the numeric evidence — a list of `{method, lag, score, p_value, direction}` objects (one per statistical method that ran, e.g. lagged cross-correlation or Granger). `get_dependency_history` omits `direction` inside its `methods[]` entries.
- **`band_low` / `band_high`** in `classify_levels_vs_band` are the min `p10` and max `p90` across the horizon window used for classification.
- When there is no data, tools return an empty list (`list[dict]` tools) or `{"available": false}` (status/calibration) or a `no_forecast` sentinel (`classify_levels_vs_band`, `get_band_position`).

## Call examples

The calls below use the bundled ETT example instance — metric `ot` (oil temperature) with segment keys of the form `dataset=ETTh1|feature=ot`. Substitute your own metric and segment keys for other instances.

```text
# Discover what is available
list_metrics()
  -> ["ot", ...]
list_segments(metric="ot")
  -> ["dataset=ETTh1|feature=ot", "dataset=ETTh2|feature=ot", ...]

# Latest forecast points for one metric/segment
get_forecast(metric="ot", segment="dataset=ETTh1|feature=ot")
  -> [{"ts": "...", "horizon_step": 1, "y_hat": ..., "p10": ..., "p50": ..., "p90": ...}, ...]

# Is the latest run fresh, and which model produced it?
get_run_status()
  -> {"available": true, "status": "success", "model_name": "baseline-seasonal-naive", ...}

# Lead/lag dependencies pointing at a target segment
get_dependencies(target_segment="dataset=ETTh1|feature=ot", metric="ot")
  -> [{"source_segment": "dataset=ETTh1|feature=hufl", "explained": true, "is_real": true,
       "lag": ..., "direction": ..., "methods": [...], ...}, ...]
```

## Freshness and degradation

The contract tables always reflect the **most recent** run — there is no versioned history except `get_dependency_history`. Before acting on a forecast, an agent should check freshness and status:

- **`get_run_status` / `get_forecast_status`** report `status`, `model_name` / `model_version`, run timings (`started_at` / `finished_at`), and — for `get_forecast_status` — `last_forecast_ts` (the latest forecast timestamp). Use these to detect a stale or `failed` run before trusting the points. A `timesfm-2.5` run whose worker was unreachable records `status=failed` and writes no points — there is no silent fallback (see [Deployment](deployment.md) and [Jobs](jobs.md)).
- **`is_sparse`** in `get_calibration` flags that the calibration metrics rest on few points (`n_points`) and should be read with caution.
- **`get_dependencies.explained`** distinguishes the two layers of a dependency record:
  - `explained: true` — the numeric evidence (`methods[]`) **and** the LLM agent's judgment (`is_real`, `confidence`, `explanation`, `caveats`, `change_note`) are present.
  - `explained: false` — a numeric dependency was found and written, but the LLM explanation was unavailable when the dependency job ran (the agent degraded gracefully). The numeric fields are still populated; the judgment fields are null/empty. Treat such records as statistical signal without a vetted verdict.

## See also

- [Jobs](jobs.md) — how forecast and dependency runs populate the tables these tools read.
- [Configuration](configuration.md) — `mcp.yml` host/port and the env-override pattern.
- [User Guide index](README.md) · [Project root](../../README.md)
