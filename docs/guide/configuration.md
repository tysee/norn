# Configuration

*Audience: operators and deployers who set up norn for an environment — local, cloud, or Kubernetes.*

norn has **no hardcoded settings**. Every value comes from one YAML file per
section plus environment-variable overrides. This page documents the config
model and every field of the four sections (`database`, `forecast`, `agent`,
`mcp`), the LLM provider matrix, and the `NORN_<SECTION>_<FIELD>` override
convention.

## Config model

- **One YAML file per section** lives in the config directory: `database.yml`,
  `forecast.yml`, `agent.yml`, `mcp.yml`.
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
  YAML; they come exclusively from environment variables (see the tables below).

```text
$NORN_CONFIG_DIR/        # default: config/
├── database.yml
├── forecast.yml
├── agent.yml
└── mcp.yml
```

## database.yml

ClickHouse connection used to read marts and write the contract tables.

| Field | Type | Description |
|---|---|---|
| `host` | string | ClickHouse host (e.g. `localhost` for the local sidecar). |
| `port` | int | ClickHouse HTTP-interface port (e.g. `8123`). |
| `user` | string | Database user. |
| `database` | string | Database holding the contract schema. |
| `secure` | bool | Use a TLS connection (`true` for secured connections). |
| `manage_schema` | bool | `true`: norn idempotently **creates** its contract tables (zero-setup). `false`: norn runs **no DDL** (INSERT-only) — you create the tables yourself; `norn print-schema` emits the canonical DDL. |

**Secret (env-only):**

| Env var | Description |
|---|---|
| `NORN_DB_PASSWORD` | Database password. Required, never placed in YAML, no default. |

**DSN override (env-only, optional):**

| Env var | Description |
|---|---|
| `NORN_CLICKHOUSE_URL` | Full ClickHouse DSN. When set, overrides the assembled connection. Unset (the default) = no override; norn uses the `host`/`port`/`user`/… fields. |

## forecast.yml

Forecast-subsystem defaults, quantiles, the TimesFM worker pointer, calibration,
and covariate (XReg) policy. Values here are platform defaults; an individual
forecast job may override `horizon` / `context_length` / `seasonality`.

| Field | Type | Description |
|---|---|---|
| `defaults.horizon` | int | Default forecast depth, in series steps (e.g. `30`). |
| `defaults.context_length` | int | Default history window length (number of points) fed to the model. |
| `defaults.seasonality` | int | Default seasonality period, in steps (e.g. `7` = weekly). |
| `quantiles` | list[float] | Forecast quantiles forming the uncertainty band, e.g. `[0.1, 0.5, 0.9]` (low / median / high). |
| `timesfm.worker_url` | string | URL of the HTTP TimesFM worker (e.g. `http://localhost:9100`). |
| `timesfm.max_context` | int | Upper bound on context length the model accepts. |
| `timesfm.max_horizon` | int | Upper bound on the model's forecast depth. |
| `calibration.n_cutoffs` | int | Number of rolling-origin cutoffs used to score forecast quality. |
| `covariates.horizon_policy` | string | `strict` (covariate `lag >= horizon`) or `ffill` (forward-fill / extend the leader). |
| `covariates.xreg_mode` | string | XReg mode, e.g. `xreg+timesfm`. |

## agent.yml

Dependency-analysis subsystem: which LLM produces explanations and how the
statistical lead/lag search behaves. **LLM keys are never placed here** — they
are env-only (see the provider table).

| Field | Type | Description |
|---|---|---|
| `provider` | string | LLM provider: `ollama` \| `openai-api` \| `openai-oauth` \| `openrouter` \| `anthropic-api`. |
| `model` | string | Model name for the chosen provider. |
| `base_url` | string \| null | Provider endpoint. Required for `ollama` (explicit, no code fallback); set to `null` for cloud providers. |
| `output_mode` | string | How structured output is obtained: `native` \| `tool` \| `prompted`. |
| `max_lag` | int | Maximum shift (lag), in series steps, probed when searching for dependencies. |
| `context_length` | int | History window length (number of points) fed to the analysis. |
| `methods` | list[string] | Statistical methods, e.g. `[lagged_cross_correlation, granger]`. |
| `granger_min_points_factor` | int | Multiplier: minimum points for the Granger test = `factor * max_lag`. |
| `granger_significance` | float | Granger p-value threshold; below this a dependency is considered significant. |

### LLM providers

The dependency agent supports five providers. Each provider's secret comes from
its own environment variable; `ollama` needs no key but does need a running
daemon and a pulled model.

| `provider` | Secret env var | Example `model` | `base_url` | Recommended `output_mode` |
|---|---|---|---|---|
| `ollama` | *(none)* | a pulled local model | local URL, e.g. `http://localhost:11434/v1` | `native` |
| `openai-api` | `OPENAI_API_KEY` | `gpt-4o-mini` | `null` | `tool` |
| `openai-oauth` | `NORN_OPENAI_OAUTH_TOKEN` | an OpenAI model | `null` | `tool` |
| `openrouter` | `OPENROUTER_API_KEY` | e.g. `anthropic/claude-sonnet-4-5` | `null` | `tool` |
| `anthropic-api` | `ANTHROPIC_API_KEY` | e.g. `claude-sonnet-4-5` | `null` | `tool` |

> **ollama:** requires the Ollama daemon running and the chosen model pulled
> (`ollama pull <model>`). The `base_url` must point at the Ollama OpenAI-compatible
> endpoint — there is no implicit fallback in code.

When the LLM is unavailable, dependency analysis **degrades explicitly**: the
numeric statistics are still written and the explanation is left empty. See
[Jobs](jobs.md) for the dependency-job behavior.

## mcp.yml

The MCP server bind address.

| Field | Type | Default | Description |
|---|---|---|---|
| `host` | string | `127.0.0.1` | Interface the MCP server listens on. `127.0.0.1` = loopback only; set to a reachable address to expose it remotely. |
| `port` | int | `9200` | TCP port of the MCP server. |

See [MCP](mcp.md) for connecting to the server.

## Environment overrides

Any field can be overridden at runtime with an environment variable named
**`NORN_<SECTION>_<FIELD>`** (env beats YAML). Section prefixes are:

| Section | Env prefix |
|---|---|
| `database.yml` | `NORN_DB_` |
| `forecast.yml` | `NORN_FORECAST_` |
| `agent.yml` | `NORN_AGENT_` |
| `mcp.yml` | `NORN_MCP_` |

Examples:

```bash
export NORN_DB_MANAGE_SCHEMA=false   # database.manage_schema
export NORN_AGENT_PROVIDER=openai-api # agent.provider
export NORN_MCP_HOST=0.0.0.0          # mcp.host (expose remotely)
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

## See also

- [Deployment](deployment.md) — required env per environment, cloud/k8s notes, the TimesFM worker.
- [Jobs](jobs.md) — forecast/dependency jobs, calibration, schema ownership.
- [MCP](mcp.md) — connecting agents to the server defined in `mcp.yml`.
- [User Guide index](README.md) · [Project README](../../README.md)

> Domain specifics (concrete metrics, marts, dashboards) live in an instance repo — e.g. `norn-crypto-instance`.
