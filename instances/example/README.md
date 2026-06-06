# instances/example — instance template (skeleton, no data)

This directory is a **copyable skeleton** for a new norn instance.
It contains all the configuration and job-definition files you need to wire up
your own data source to the norn forecasting platform, but ships **no data,
no ingestion code, and no Python**.

For a fully working instance with real data, see **`instances/ett`** (the
ETT Electricity Transformer Temperature dataset).

---

## Anatomy

| Directory / file          | Purpose |
|---------------------------|---------|
| `config/`                 | Instance-specific platform config (all 5 YAML files). Point `NORN_CONFIG_DIR` here. |
| `config/database.yml`     | ClickHouse connection — set your DB name and host. |
| `config/forecast.yml`     | Forecast defaults, TimesFM settings, calibration, retention. |
| `config/agent.yml`        | Dependency-analysis LLM provider, lag search parameters. |
| `config/mcp.yml`          | MCP server listen address. |
| `config/scheduler.yml`    | Built-in scheduler HTTP-API address and retry policy. |
| `forecasts/`              | ForecastJob YAML files (one per metric/model combination). |
| `forecasts/deps/`         | DependencyJob YAML files (one per candidate lead/lag pair). |
| `dbt/`                    | dbt project that shapes your raw data into `mart_metric`. |
| `dbt/models/sources.yml`  | Raw-data source declaration (bring your own table). |
| `dbt/models/mart_metric.sql` | Long-format mart expected by norn (ts, metric_name, value, segment_key). |
| `dbt/models/fct_orders.sql`  | Purpose-built view for the forecast jobs (filtered, metric column named after `metric:` in the job YAML). Rename/replace for your metric. |
| `dbt/models/schema.yml`   | Minimal column tests for the mart. |
| `deploy/jobs.yml`         | Scheduler manifest — what runs, when, and how. |
| `deploy/crontab.sample`   | Host-cron alternative for environments without the built-in scheduler. |

---

## How to adapt this template in 5 steps

1. **Rename** — copy the whole `instances/example/` directory and rename it
   (e.g. `instances/myproject/`). Update `dbt_project.yml` and `profiles.yml`
   to use your own project and profile names.

2. **Point dbt at your raw data** — edit `dbt/models/sources.yml` to declare
   your raw ClickHouse table, then rewrite `dbt/models/mart_metric.sql` to
   produce the `(ts, metric_name, value, segment_key)` long-format contract
   (see the TODO comments in both files). Run `dbt run` to materialise the mart.

3. **Edit the forecast jobs** — update `forecasts/orders_baseline.yml` (and
   optionally `orders_timesfm.yml`) to match your `metric`, `source` table,
   `grain`, `dimensions`, and `horizon`. Add or remove job files as needed.
   If you plan to discover lead/lag covariates, fill in
   `forecasts/deps/visits_orders.yml` with your candidate pair.

4. **Set `NORN_CONFIG_DIR`** — when running any norn command, export the env
   var so norn reads your instance config instead of the platform default:
   ```
   export NORN_CONFIG_DIR=instances/myproject/config
   uv run norn forecast instances/myproject/forecasts/orders_baseline.yml
   ```

5. **Mount for the scheduler** — use `NORN_JOBS_DIR` to point the container at
   your instance root so `/jobs/forecasts/...` paths in `deploy/jobs.yml` resolve:
   ```
   NORN_JOBS_DIR=../instances/myproject \
     docker compose -f docker-compose.services.yml --profile scheduler up -d scheduler
   ```
   The scheduler reads the manifest from `/jobs/deploy/jobs.yml` and forecast
   jobs from `/jobs/forecasts/...`.

---

## Notes

- `instances/ett` is the **full working example** with real data, ingestion,
  and end-to-end tests. Read it when you need a concrete reference.
- This directory (`instances/example`) is the **skeleton to copy**. It never
  contains real data or ingestion code.
- Secrets (DB password, LLM API keys) are **never** placed in YAML files.
  Pass them via environment variables (see comments in each config file).
