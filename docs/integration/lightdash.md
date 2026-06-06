# Integrating norn with Lightdash

> **Platform invariant.** norn is a vendor-neutral, domain-agnostic forecasting
> platform: it writes multi-segment metric forecasts to your warehouse via a
> generic contract (`forecast_point` / `forecast_segment`). The platform ships no
> built-in domain defaults (no metrics, dimensions, dashboards, or model choices);
> domain specifics live in an instance repo (the open-source example instance is
> `norn-ett-instance`, attached as a submodule at `instances/ett`). Any concrete
> metric or segment below is a **labeled example**, not a platform requirement.

norn does **not** ship or fork Lightdash. norn writes forecast rows to ClickHouse
(`forecast_point`); you point your existing Lightdash project at those tables.

## Steps

1. In your dbt project add a model `actual_vs_forecast` that joins your metric mart
   to `forecast_point` on `(metric_name, segment_key, ts = forecast_ts)`. The metric
   mart and `metric_name` values are domain-specific and come from your instance
   (e.g. the ETT example instance exposes the metric `ot` — oil temperature — over
   segments like `dataset=ETTh1|feature=ot`, joining the `mart_metric`/`fct_ot`
   marts to `forecast_point`; see `instances/ett/dbt/models/actual_vs_forecast.sql`).
2. Expose `y_actual`, `y_hat`, `p10`, `p90`, `error_abs` as Lightdash metrics.
3. Build a chart: actual line + forecast line + p10/p90 band per segment.

## Connection

Lightdash connects to the **same ClickHouse** norn writes to. norn never touches
Lightdash's Postgres. Domain-specific dashboards live in the corresponding instance
repo (e.g. the ETT example instance, `norn-ett-instance` at `instances/ett`), not in
this platform repo.

## The local Lightdash stack

`deploy/docker-compose.yml` (the **infra** file) ships a complete self-hosted
Lightdash next to ClickHouse:

| Service | Container | Purpose |
| --- | --- | --- |
| `lightdash` | `norn-lightdash` | the Lightdash server on `:8080` (image is amd64-only; emulated on Apple Silicon) |
| `lightdash-db` | `norn-lightdash-db` | Lightdash's own Postgres (projects, charts, users) |
| `headless-browser` | `norn-headless-browser` | browserless Chrome — Lightdash uses it for image exports / scheduled deliveries |
| `minio` (+ `minio-init`) | `norn-minio` | S3-compatible storage for export artifacts |
| `lightdash-init` (profile `setup`) | one-shot | the headless bootstrap below |

### Fast local setup (zero UI forms)

The bootstrap (`deploy/bootstrap-lightdash.sh`, baked into
`deploy/lightdash-init.Dockerfile`) replaces every manual setup form with API
calls + a CLI deploy. The **instance** supplies the domain policy via its
env-file (`LD_PROJECT_NAME`, `DBT_PROJECT_HOST_DIR`, admin bootstrap values —
see `instances/ett/deploy/.env.example`):

```bash
# from the platform repo root, ETT example instance:
cp instances/ett/deploy/.env.example instances/ett/deploy/.env
docker compose -f deploy/docker-compose.yml \
  --env-file instances/ett/deploy/.env --profile setup run --rm lightdash-init
```

What it does, in order: waits for `/api/v1/health` → registers the first admin
(`LD_ADMIN_*`) → names the organization → **mints a personal access token** →
runs `dbt run` for the instance's project → `lightdash deploy --create`, which
pushes the ClickHouse warehouse connection and the compiled dbt manifest. No
warehouse/dbt UI form is ever filled by hand, and the script is **idempotent**
(re-running re-deploys instead of re-creating). After it finishes, Lightdash is
at `http://localhost:8080` with the instance's explores ready.

## MCP: driving Lightdash from an LLM

Lightdash's own built-in MCP/AI features are gated to Lightdash Cloud /
Enterprise — they are not available on the self-hosted OSS image. For the local
stack we use the **community MCP server**
[`lightdash-mcp`](https://github.com/poddubnyoleg/lightdash_mcp) (PyPI), which
talks to the plain REST API with a personal access token and — unlike the
read-only official surface — includes **write tools**: create/update charts,
dashboards, tiles, and spaces.

### Setup

```bash
uv tool install lightdash-mcp          # installs the `lightdash-mcp` command
```

You need two values:

- **A personal access token** — create one in the UI: *Settings → Personal
  access tokens*. (The bootstrap mints its own PAT for the deploy but does not
  print it — make a separate one for the MCP.)
- **The project UUID** — visible in any project URL
  (`/projects/<uuid>/...`) or via `GET /api/v1/org/projects`.

Register the server with your MCP client, e.g. Claude Code:

```bash
claude mcp add lightdash \
  --env LIGHTDASH_URL=http://localhost:8080 \
  --env LIGHTDASH_TOKEN=<your-PAT> \
  --env LIGHTDASH_PROJECT_UUID=<project-uuid> \
  -- lightdash-mcp
```

The same three env vars work for any MCP client (Claude Desktop, Cursor, …).

### Making charts with an LLM

With the MCP connected, chart-building becomes a conversation. The loop the
agent runs under the hood:

1. **`list-explores`** → find the explore (e.g. `actual_vs_forecast`).
2. **`get-explore-schema`** → learn its dimensions/metrics (`ts`,
   `segment_key`, `y_actual`, `y_hat`, `p10`, `p90`).
3. **`run-raw-query`** → sanity-check the data shape before charting.
4. **`create-chart`** → save the chart (Lightdash/ECharts config: series,
   axes, colors), **`run-chart-query`** to verify it returns rows.
5. **`create-dashboard`** / **`create-dashboard-tile`** → compose dashboards.

A prompt that works well against the ETT example instance:

> Using the `actual_vs_forecast` explore, build a chart for
> `segment_key = dataset=ETTh1|feature=ot`: `y_actual` as a line,
> `y_hat` as a second line, and a `p10`–`p90` band, over `ts`.
> Save it as "OT: actual vs forecast — ETTh1" and add it to the
> "ETT — Forecast Validation" dashboard.

The actual-vs-forecast charts in the ETT example (see the screenshots in
`instances/ett/README.md`) were produced exactly this way. Two habits keep
LLM-built charts honest: always let the agent run the chart query after
creating it (step 4), and keep chart definitions reviewable in a dashboard the
team owns — the MCP writes through the same API the UI uses, so everything
stays editable by humans.

## See also

- [norn-forecast](../guide/forecast/README.md) — the package that writes `forecast_point`.
- [Deployment](../guide/deployment.md) — bringing up the infra stack, compose split rules.
- [ETT example instance](../../instances/ett/README.md) — a complete worked pipeline with Lightdash dashboards.
