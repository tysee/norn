# Configuration

_Audience: operators and deployers who set up norn for an environment — local, cloud, or Kubernetes._

norn has **no hardcoded settings**. Every value comes from one YAML file per
section plus environment-variable overrides. This page documents the **config
model** — file layout, override priority, secrets, instance config dirs. The
field-by-field key tables live on the **package pages** that own each section
(linked below).

## Config model

- **One YAML file per section** lives in the config directory: `database.yml`,
  `forecast.yml`, `agent.yml`, `mcp.yml`, `scheduler.yml`.
- **Config directory** is `config/` by default, overridable with the env var
  **`NORN_CONFIG_DIR`** (point it at your config dir in containers / k8s where
  the working directory is not the repo root).
- **Priority: env > yaml.** An environment variable always wins over the value
  in the YAML file. (Internally, init arguments used by tests take precedence
  over both, but that is not a runtime source.)
- **No hidden Python defaults.** Fields have no fallback values in code: if a
  required key is missing from both env and YAML, norn raises a clear
  **`ValidationError`** naming the missing field — it does not silently
  substitute a default.
- **Missing dir/file fails loudly.** If a section's YAML file is not found under
  `NORN_CONFIG_DIR`, norn raises a **`FileNotFoundError`** telling you which file
  is missing and where it looked — rather than a generic "field required".
- **Secrets are env-only.** Passwords and LLM provider keys are never read from
  YAML; they come exclusively from environment variables (see
  [Secrets](#secrets-env-only)).

```text
$NORN_CONFIG_DIR/        # default: config/
├── database.yml
├── forecast.yml
├── agent.yml
├── mcp.yml
└── scheduler.yml
```

## Sections — where each is documented

Each section file is owned by one package; its page carries the full key table:

| Section file    | Owning package page                   | What it configures                                                            |
| --------------- | ------------------------------------- | ----------------------------------------------------------------------------- |
| `database.yml`  | [norn-core](core/README.md)           | ClickHouse connection, `manage_schema` ownership toggle                       |
| `forecast.yml`  | [norn-forecast](forecast/README.md)   | job defaults, quantiles, TimesFM worker, calibration, XReg policy, table TTL  |
| `agent.yml`     | [norn-agent](agent/README.md)         | LLM provider/model, statistical methods, Granger thresholds, agent worker URL |
| `mcp.yml`       | [norn-forecast](forecast/README.md)   | MCP server bind address (`127.0.0.1:9200` by default)                         |
| `scheduler.yml` | [norn-scheduler](scheduler/README.md) | scheduler HTTP API bind address, retry/misfire policy                         |

## Environment overrides

Any field can be overridden at runtime with an environment variable named
**`NORN_<SECTION>_<FIELD>`** (env beats YAML). Section prefixes are:

| Section         | Env prefix        |
| --------------- | ----------------- |
| `database.yml`  | `NORN_DB_`        |
| `forecast.yml`  | `NORN_FORECAST_`  |
| `agent.yml`     | `NORN_AGENT_`     |
| `mcp.yml`       | `NORN_MCP_`       |
| `scheduler.yml` | `NORN_SCHEDULER_` |

Examples:

```bash
export NORN_DB_MANAGE_SCHEMA=false      # database.manage_schema
export NORN_AGENT_PROVIDER=openai-api   # agent.provider
export NORN_MCP_HOST=0.0.0.0            # mcp.host (expose remotely)
export NORN_SCHEDULER_HOST=0.0.0.0      # scheduler.host (expose the control API)
```

**Nested fields** use a double-underscore (`__`) between the parent and child
key, after the section prefix:

```bash
export NORN_FORECAST_TIMESFM__WORKER_URL=http://timesfm:9100  # forecast.timesfm.worker_url
export NORN_FORECAST_COVARIATES__HORIZON_POLICY=ffill         # forecast.covariates.horizon_policy
```

Some settings have dedicated, well-known env vars (set via `validation_alias`)
rather than the `NORN_<SECTION>_<FIELD>` form:

- `NORN_DB_PASSWORD` — `database.password` (env-only secret).
- `NORN_CLICKHOUSE_URL` — `database.dsn` (full DSN override).

## Secrets (env-only)

All secrets in one place — none of these ever appear in YAML:

| Env var                   | Secret for                                                             |
| ------------------------- | ---------------------------------------------------------------------- |
| `NORN_DB_PASSWORD`        | ClickHouse password. Required, no default.                             |
| `NORN_CLICKHOUSE_URL`     | Optional full DSN override (may embed credentials; treat as a secret). |
| `OPENAI_API_KEY`          | LLM provider `openai-api`                                              |
| `NORN_OPENAI_OAUTH_TOKEN` | LLM provider `openai-oauth`                                            |
| `OPENROUTER_API_KEY`      | LLM provider `openrouter`                                              |
| `ANTHROPIC_API_KEY`       | LLM provider `anthropic-api`                                           |
| _(none)_                  | LLM provider `ollama` — needs a running daemon, not a key              |

The full provider matrix (models, `base_url`, `output_mode` recommendations and
degradation behavior) lives on the [norn-agent page](agent/README.md); the
OAuth bearer flow for `openai-oauth` is documented
[there](agent/README.md#the-openai-oauth-flow-bearer-token-instead-of-an-api-key) too.

### Where to keep them (so they never reach the repo)

Pick by scenario — all three locations are outside version control (`.env` and
`.envrc` are gitignored; verify with `git check-ignore` if in doubt):

| Scenario | Put secrets in |
| --- | --- |
| Local CLI runs (`norn forecast` / `deps` / …) | your shell profile (`~/.zshenv`), or a [direnv](https://direnv.net) `.envrc` in the repo root |
| Docker services (compose) | `deploy/.env` for the stack (compose interpolation) and `deploy/agent.env` for the LLM judge's provider **key** (loaded by the `agent` service; copy `agent.env.example`). Settings stay in `config/*.yml` — the services mount it live. Both env files gitignored; only the `*.example` files are tracked |
| Cloud / Kubernetes | platform secret stores (k8s `Secret` → container env), see [Deployment](deployment.md) |

Even if a key is pasted into a YAML file by mistake, it is **never read from
there** — the settings models accept secrets exclusively from environment
variables.

## Instance config dirs

An instance can ship its own config directory alongside its jobs and dbt models.
Set `NORN_CONFIG_DIR` to the instance's config path and all five section files
are loaded from there instead of the repo root `config/`:

```bash
NORN_CONFIG_DIR=instances/example/config uv run norn forecast instances/example/forecasts/orders_baseline.yml
```

The priority rule is unchanged: **env > yaml**. Any `NORN_<SECTION>_<FIELD>`
environment variable still overrides the value in the instance's YAML file —
there is no difference in override semantics between the default `config/` and
an instance-owned directory.

[`instances/example/config/`](../../instances/example/config/) is the canonical
template for a new instance's config directory: it contains all five section
files with sensible starting values and explanatory comments.

## See also

- [Deployment](deployment.md) — required env per environment, cloud/k8s notes, the TimesFM worker.
- [Jobs](jobs.md) — forecast/dependency jobs, calibration, schema ownership.
- [MCP tool reference](forecast/mcp.md) — connecting agents to the server defined in `mcp.yml`.
- [User Guide index](README.md) · [Project README](../../README.md)

> Domain specifics (concrete metrics, marts, dashboards) live in an instance repo — e.g. the ETT example instance (`norn-ett-instance`, metric `ot`, marts `mart_metric` / `fct_ot`).
