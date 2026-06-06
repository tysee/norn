# Norn — monorepo, ERD and tech stack

*Date: 29 May 2026. Accompanies `erd.mermaid` and `architecture.mermaid`.*

> **Platform invariant.** norn is a vendor-neutral, domain-AGNOSTIC forecasting platform: multi-segment forecasting of metrics and dependency discovery on top of any warehouse via a generic contract (`forecast_point`/`forecast_segment`), with configurable model/provider/DB and an MCP contract. The platform code (`packages/*`, `cli`) carries NO domain defaults — no built-in metrics, symbols, dimensions, ingestion formats, dashboards, prompts, nor any choice of LLM model. All domain specifics live in a separate instance repo (`norn-crypto-instance` — the first dogfood instance, attached as a submodule). The GTM focus (the first target vertical) is delivery/marketplace/e-commerce: this is a market strategy, NOT a platform default. Any concrete domain in this document (delivery KPIs such as delivered_orders/GMV, crypto symbols BTC/TON, dimensions, transformations, model choice) is a flagged EXAMPLE pointing at an instance/vertical, not a platform requirement; domain details live in the instance repo.

A plug-and-play sidecar to Lightdash: with a single command an analyst stands up forecasts and dependency analysis on top of their existing `dbt + ClickHouse + Lightdash` stack. We do **not fork Lightdash and do not write our own BI** — we add three layers alongside it.

---

## 1. Monorepo layout (3 parts + CLI)

```text
norn/                    # repository tysee/norn
├── packages/
│   ├── agent/          # 1) Dependency-analysis agent (pi.dev / PydanticAI)
│   ├── forecast/       # 2) Forecast service (TimesFM 2.5 worker, FastAPI)
│   └── integration/    # 3) Glue: dbt + Lightdash + ClickHouse
├── cli/                # one-command orchestrator: `norn up`
├── deploy/             # docker-compose: sidecar next to Lightdash
├── forecasts/          # YAML forecast-job registry (no UI at the start)
└── pyproject.toml      # workspace (uv / hatch), shared lint/types
```

The three parts are exactly the three focus layers from the strategy: **metric description** (integration), **forecasting** (forecast), **dependency discovery** (agent).

---

## 2. Tech stack and the "fewer dependencies" principle

Base rule: use off-the-shelf if it does not impose unnecessary constraints; our own code is only glue. Each dependency must "earn its place".

| Part | Language / runtime | Off-the-shelf (reused) | Our code (glue) |
|-------|----------------|---------------------------|-----------------|
| integration | Python 3.14+ | `dbt-core` + `dbt-clickhouse`; reading metrics from the dbt `manifest.json` (or the Lightdash API); `clickhouse-connect` | mapping dbt metrics → `metric_definition`, generating the `actual_vs_forecast` dbt model |
| forecast | Python (worker env, see §5) | `timesfm` (2.5) + `torch`; `clickhouse-connect`; `FastAPI` for the API; `pydantic` for configs | extract→group→inference→write-back; sparse policy |
| agent | Python 3.14+ | agent framework (pi.dev / PydanticAI); `numpy`/`scipy`/`statsmodels` (lagged corr, Granger, MI); provider LLM SDK | analysis orchestration, building explanations with caveats |
| cli | Python 3.14+ | `typer` (or stdlib `argparse` if we want 0 extra dependencies) | `norn init` / `norn up` |
| metadata | — | Postgres (later); at the start — `forecasts/*.yml` + tables in ClickHouse | — |

Deliberately NOT pulling in: our own scheduler (we use the system `cron` in `deploy/`), our own ORM at the start (configs are YAML, the run log lives in ClickHouse), our own dashboard engine (Lightdash), our own transform (dbt).

---

## 3. Stores and where the ERD entities live

Legend for `erd.mermaid`:

- **`[LD]` Lightdash Postgres** — projects and dbt metrics. **Read-only** (via `manifest.json` or the Lightdash API), not owned by us.
- **`[CH]` ClickHouse** — `mart_metric` (facts, built by dbt), `forecast_point` (forecast output), `actual_vs_forecast` (dbt view). The analytical layer.
- **`[META]` addon Postgres** — `project`, `connection`, `metric_definition`, `forecast_job/run/segment`, `dependency_*`.

**Important MVP caveat.** A full `[META]` Postgres is needed only once a UI / many users appear. At the start (as in the MVP spec) persistence is simpler:

- `forecast_job` / `metric_definition` → `forecasts/*.yml` in the repository;
- `forecast_run` / `forecast_segment` / `forecast_point` → ClickHouse tables;
- `dependency_*` → ClickHouse or JSON artifacts.

That is, the ERD describes the **logical** model; we introduce the relational `[META]` store in Phase 1+, when we add dependencies/MCP and a UI. This keeps the number of infra dependencies minimal at the start (only ClickHouse, which is there anyway).

---

## 4. One-command UX (plug-and-play)

```text
norn init       # discover dbt metrics (manifest.json/Lightdash),
                # propose forecast jobs -> forecasts/*.yml
norn up         # stand up the sidecar: forecast worker + agent (FastAPI),
                # run the forecast, write forecast_point to ClickHouse,
                # generate the dbt actual_vs_forecast and refresh Lightdash
```

`deploy/docker-compose.yml` runs the sidecar pointing at the **existing** ClickHouse and Lightdash (via env). The analyst does not need to configure anything by hand beyond the DSN.

### Local BI stack (debugging)

`deploy/docker-compose.yml` brings up locally: ClickHouse + Lightdash (+ its
Postgres + headless browser) + a generic dbt project `deploy/dbt/` (profiles → ClickHouse,
models `mart_metric`, `actual_vs_forecast`). The TimesFM worker is a separate torch-pinned
container (`deploy/timesfm.Dockerfile`); the forecast layer talks to it over HTTP behind a
`Forecaster` interface (baseline remains the fallback). Data seeding — raw
datapoints (ingestion format is the instance's choice; crypto instance: `raw_candles`) — is
separate, outside the platform.

**MCP layer (agents):** `norn mcp` brings up a FastMCP server (streamable-http) with
MCP tools (11): get_forecast / get_expected_range / classify_levels_vs_band /
get_divergence / get_calibration (incl. is_sparse) / get_dependencies (explained flag +
numeric fallback on LLM degradation) / get_dependency_history / get_run_status /
get_forecast_status / list_metrics / list_segments on top of the tables `forecast_point` /
`forecast_segment` / `forecast_run` / `metric_dependency` / `dependency_explanation`.
Discovery (list_*) and status/freshness (get_*_status) let an agent find series and
assess forecast freshness. "Lightdash for humans, MCP for agents".
`get_dependencies` (example (crypto instance): BTC↔TON) — Plan 5.

**Dependency agent (`packages/agent`):** a PydanticAI dependency-analysis agent. The methods
(lagged cross-correlation + Granger on a domain transformation of the series — example domain
transformation (crypto instance): log-returns) produce evidence → the agent judges reality and
explains → `metric_dependency` (numbers) + `dependency_explanation` (decision). `norn deps
<job.yml>`; MCP `get_dependencies` returns both the numbers and the agent's decision. Tests run on the PydanticAI
`TestModel` (no real LLM). The lag is a future TimesFM covariate (XReg).

**The agent's LLM provider and model** are configurable (`config/agent.yml` → `provider` / `model`):
ollama (local), openai-api, openai-oauth (bearer), openrouter, anthropic-api. The concrete
model and provider are the instance's choice in `config/agent.yml`; the platform has NO default LLM model.
Secrets come from env (OPENAI_API_KEY / NORN_OPENAI_OAUTH_TOKEN / OPENROUTER_API_KEY /
ANTHROPIC_API_KEY). For local Ollama: a running daemon on :11434 + `ollama pull <model>`
(model from the instance config). When the provider is unavailable/incorrect, `norn deps` degrades
(writes metric_dependency, without an explanation) instead of crashing.

---

## 5. Python 3.14+ compatibility (an honest risk)

Our code targets **Python 3.14+**, but two dependencies have historically lagged behind fresh Python releases:

- **`torch` / `timesfm`** — wheels for 3.14 may arrive with a delay.
- **`dbt-core`** — support for new minor Python versions usually catches up only later.

Mitigation — the monorepo makes this painless:

1. **forecast** runs in its own container with a pinned interpreter for torch (e.g. 3.12/3.13), communicating over FastAPI/HTTP — the rest of our code stays on 3.14+.
2. **dbt** is invoked as a **subprocess** (CLI) rather than imported into our process → dbt's Python version is decoupled from ours.
3. `integration` and `agent` (pure Python + numpy/scipy) — on 3.14+ without issues.

Check before starting: availability of `torch`/`timesfm` wheels and Python support in `dbt-clickhouse` at build time (an open question in the spike).

---

## 6. Configuration (YAML-native)

All generic platform settings live in a central `config/` (split by concern:
`database.yml`/`forecast.yml`/`agent.yml`/`mcp.yml`), read by the typed layer
`norn_core.config` (pydantic-settings). Priority: **env > YAML > default**. Secrets
(DB password, API keys) live only in env (`NORN_DB_PASSWORD`, `NORN_CLICKHOUSE_URL`).
`NORN_CONFIG_DIR` overrides the path. Domain values (metrics/symbols) do NOT go into
the platform config — that is the instance.

Magic constants are eliminated: baseline intervals are derived from `forecast.quantiles`
(normal approximation), the Granger significance/threshold from `agent.*`, the TimesFM quantile
columns are derived from the requested quantiles. Numeric tolerances (eps) are named constants.

**Covariates (XReg):** a forecast job may declare covariates (metric/segment/lag) or
use_dependencies (take confirmed dependencies from metric_dependency). The runner builds a
timestamp-aligned leader series over context+horizon (policy strict|ffill from config) and
passes it to TimesFM as dynamic_numerical_covariates (forecast_with_covariates). Without covariates —
a regular forecast (default, unchanged). Baseline ignores covariates.

**Config — YAML-native with no hidden defaults:** settings fields have no Python defaults; the value
is taken from `config/<section>.yml` (or an env override), and a missing required key → an explicit
`ValidationError` at startup. The DB secret (`password`) comes only from env `NORN_DB_PASSWORD`. The LLM
inference mode (`agent.output_mode`: native|tool|prompted) and `agent.base_url` are explicit configuration, without
fallbacks in code. **LLM degradation is explicit:** `judge_dependencies` raises `LLMUnavailable`,
`analyze_dependencies` catches it at the boundary (ERROR log with traceback), returns an `AnalysisResult`
(`explained=False` + reason), and the CLI prints `⚠ LLM explanation skipped: …`; the statistics
(`metric_dependency`) are always written.

**Ownership of the contract tables' schema (`database.manage_schema`):** norn is warehouse-table-native,
dbt-optional. `true` (default) — norn idempotently creates its own contract tables in its DB
(zero-setup, greenfield/local). `false` — norn does NOT execute DDL (INSERT only); the tables are
provisioned by the user via their own dbt/migrations, the canonical DDL is printed by `norn print-schema`; before
writing, norn runs a pre-flight check and, if the tables are missing, fails explicitly with `ContractSchemaMissing`.
This way the platform does not impose runtime DDL on a governed store. dbt is the typical, but not mandatory,
way to build both the mart and these tables.

**Storing the contract tables as they grow:** one table per contract type, but with
`PARTITION BY toYYYYMM(created_at)` and a configurable `TTL` (`forecast.retention_months`,
default 12 months; 0 = no TTL). This is idiomatic for ClickHouse (we do not split into separate tables).
**Upgrading existing tables:** ClickHouse does not add `PARTITION BY` via `ALTER` —
already-created tables require recreation (drop + `norn schema-apply`); forecast data is
reproducible by re-running the jobs, so this is a safe, routine step. `TTL` can be added separately
with `ALTER TABLE ... MODIFY TTL ...`.

---

## 7. Relation to the diagrams

- `erd.mermaid` — entities and relationships (the logical data model, store legend).
- `architecture.mermaid` — the sidecar component diagram: CLI → 3 packages → ClickHouse / Lightdash / dbt / LLM.

Both files render in Cowork; open their cards to see the diagrams.
