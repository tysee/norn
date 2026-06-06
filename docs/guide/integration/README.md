# norn-integration

*Audience: operators and integrators who provision the contract tables — or own them externally — and anyone who needs the canonical column definitions.*

`norn-integration` owns the **canonical DDL of the contract tables** — the
shared language between the forecast writers and every consumer (dbt,
Lightdash, MCP). The five generic tables it defines are where norn writes its
results and where dashboards and the MCP tool surface read them, so this one
schema is the stable interface the rest of the platform is written against.
The DDL itself lives in a single source of truth — `schema.sql`, shipped inside
the package — and the package's only job is to load that contract and (when
asked) apply it idempotently to ClickHouse.

---

## Functionality

### The five contract tables

All tables are `ENGINE = MergeTree`, `PARTITION BY toYYYYMM(created_at)`, and
carry a `created_at DateTime DEFAULT now()`. The columns below are verified
against `schema.sql`.

| table | role | key columns |
|---|---|---|
| `forecast_run` | Registry of forecasting-pipeline runs — one row per run; the root that points and quality metrics tie back to. Ordered by `(forecast_run_id, started_at)`. | `forecast_run_id`, `forecast_job`, `status`, `model_name`, `model_version`, `started_at`, `finished_at` (nullable), `segments_total`, `segments_skipped`, `error` (nullable) |
| `forecast_point` | The forecast values themselves — one point per (metric, segment, horizon step), with the central estimate, the interval, and the actual filled in later for scoring. Ordered by `(metric_name, segment_key, forecast_ts)`. | `forecast_run_id`, `metric_name`, `segment_key`, `forecast_ts`, `horizon_step`, `y_hat`, `p10`, `p50`, `p90`, `y_actual` (nullable), `model_name` |
| `forecast_segment` | Aggregated forecast quality per segment within a run — error metrics, interval coverage, series size, and a sparsity flag. Ordered by `(metric_name, segment_key, forecast_run_id)`. | `forecast_run_id`, `metric_name`, `segment_key`, `n_points`, `is_sparse`, `wape`, `mape`, `coverage`, `bias` |
| `metric_dependency` | Detected lead/lag links between a metric's segments — the numeric output of the dependency-analysis pipeline. A row is a directed link `source_segment → target_segment` with its lag, method, strength, direction, and significance over a window. Ordered by `(metric_name, target_segment, source_segment, created_at)`. | `analysis_run_id`, `metric_name`, `source_segment`, `target_segment`, `method`, `lag`, `score`, `direction`, `p_value` (nullable), `confidence`, `window_start`, `window_end` |
| `dependency_explanation` | The LLM's interpretation of a link from `metric_dependency` — a verdict on whether the link is real, plus prose and the model used. Ordered by `(metric_name, target_segment, source_segment, created_at)`. | `analysis_run_id`, `metric_name`, `source_segment`, `target_segment`, `lag`, `direction`, `is_real`, `confidence`, `explanation`, `caveats`, `change_note`, `llm_model` |

For which command writes each table and what the quality columns mean, see
[Jobs](../jobs.md).

### apply / print-schema flow

The package exposes the contract two ways:

- **`schema_sql(retention_months)`** returns the DDL text, read from the
  `schema.sql` resource (the source of truth for table structure). The CLI's
  `norn print-schema` emits exactly this to stdout so you can feed it into your
  own migrations or dbt models.
- **`apply_schema(client, retention_months)`** splits the contract on `;` into
  individual statements and runs each against the given ClickHouse client. The
  CLI's `norn schema-apply` calls this path.

Before a write, runs go through **`prepare_schema(client, manage_schema, retention_months)`**:
with `manage_schema=true` it calls `apply_schema`; with `manage_schema=false` it
runs **no DDL** and instead checks that every contract table exists, raising
`ContractSchemaMissing` (listing the missing tables and pointing you at
`norn print-schema`) if any is absent.

### Idempotency

Every statement in `schema.sql` is `CREATE TABLE IF NOT EXISTS`, so applying the
schema is safe to repeat — it is both the initialization and the migration entry
point for the platform's store. The set of table names is derived from the same
SQL (`required_tables()` parses it) rather than kept in a second list, so there
is no drift between what is created and what the pre-flight check looks for.

### Partitioning, TTL, and the upgrade path

Each table partitions by month of `created_at` and carries an **optional TTL**
controlled by `forecast.retention_months`:

- `retention_months > 0` substitutes the `{RETENTION_MONTHS_TTL}` token in
  `schema.sql` with `TTL created_at + INTERVAL N MONTH`.
- `retention_months == 0` strips the token — partitioning without
  auto-deletion (no TTL).

Table names and structure do **not** depend on retention, which is why
`required_tables()` reads the SQL with `retention_months=0`.

**Upgrading existing tables:** ClickHouse cannot add `PARTITION BY` via `ALTER`,
so a table created under an older schema must be **dropped and recreated** (drop
+ `norn schema-apply`). Forecast data is reproducible by re-running the jobs, so
this is a safe, routine step. A TTL change alone does not need a recreate — it
can be applied in place with `ALTER TABLE ... MODIFY TTL ...`.

---

## Configuration

This package reads two settings owned elsewhere; it does not define them.

### Schema ownership — `database.manage_schema`

The toggle lives in `database.yml` (documented on
[../core/README.md](../core/README.md)). It decides who provisions the contract
tables, and this package implements both modes via `prepare_schema`:

- **`manage_schema: true`** — norn owns the schema. `norn schema-apply` runs
  `apply_schema` to create the tables (idempotent `CREATE … IF NOT EXISTS`), and
  forecast/dependency runs can assume the tables exist. Typically local/dev or
  greenfield.
- **`manage_schema: false`** — norn runs **no DDL** and is INSERT-only. You own
  the schema externally (dbt, migrations); `norn print-schema` emits the
  canonical DDL to feed into them. `norn schema-apply` refuses to act, and the
  pre-flight check raises `ContractSchemaMissing` if the tables are not present.
  This is the recommended mode for cloud/managed ClickHouse.

### Retention — `forecast.retention_months`

Lives in `forecast.yml` (documented on
[../forecast/README.md](../forecast/README.md)). This package consumes it as the
`retention_months` argument to `schema_sql` / `apply_schema` / `prepare_schema`,
where it drives the TTL substitution described above (`0` = no TTL). It affects
only the TTL clause — never table names or structure.

---

## See also

- [../core/README.md](../core/README.md) — the ClickHouse client and
  `database.yml` (including `manage_schema`).
- [../jobs.md](../jobs.md) — which command writes each contract table, the
  quality columns, and schema-ownership modes.
- [../deployment.md](../deployment.md) — `print-schema` / `schema-apply` and
  letting your platform own the schema in cloud/Kubernetes.
- [../../erd/monorepo-and-data-model.md](../../erd/monorepo-and-data-model.md) —
  the storage model, partitioning + TTL, and the drop-and-recreate upgrade path.
