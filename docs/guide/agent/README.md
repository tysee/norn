# norn-agent

*Audience: contributors working on the dependency subsystem, and operators who run dependency jobs or the LLM judge as a service.*

`packages/agent` (`norn_agent`) is the platform's **lead/lag dependency
discovery** layer. Given two segments of a metric, it asks whether one *leads*
the other — and answers in two stages: first **statistical evidence**
(lagged cross-correlation and a Granger causality test), then an **LLM judge**
that reads that evidence and writes an explained, calibrated verdict on whether
the dependency is real or spurious. The two stages are decoupled on purpose: the
numbers are always produced and stored, while the judge is optional. When no LLM
is reachable the subsystem **degrades gracefully** — the statistical evidence is
still written and the run is reported as unexplained, never as an error. The
package is domain-neutral: the concrete metric, mart, and segments come from the
job, not from the platform.

## Functionality

### The dependency-job contract

A run is described by a `DependencyJob` (`contract.py`) — the same model the
[CLI](../jobs.md#dependency-jobs) validates a `deps/*.yml` file against:

| Field            | Type             | Default       | Meaning                                                                 |
| ---------------- | ---------------- | ------------- | ----------------------------------------------------------------------- |
| `source_segment` | string           | *(required)*  | Candidate leader segment.                                               |
| `target_segment` | string           | *(required)*  | Segment whose movement we want to explain.                              |
| `metric`         | string           | *(required)*  | The metric (`metric_name`) read for both segments. No default — the platform is domain-agnostic. |
| `mart`           | string           | `mart_metric` | Long store both series are read from.                                   |
| `max_lag`        | int \| null      | `agent.max_lag` | Maximum shift (lag), in series steps, probed when searching for a relationship. |
| `context_length` | int \| null      | `agent.context_length` | History window length (points) fed to the analysis.            |
| `methods`        | list[string] \| null | `agent.methods` | Statistical methods to run, by name from the `METHODS` registry.   |

`DependencyJob.from_yaml(path)` loads and validates a job file;
`.resolved()` fills the three unset tunables (`max_lag`, `context_length`,
`methods`) from the `agent` config section — an explicit job value always wins.

A single pass is orchestrated by `analyze_dependencies(job, client, agent=None)`
(`analyze.py`): it reads the last `context_length` points of each segment's
series, aligns them on common timestamps, runs the selected methods, writes the
evidence, hands it to the judge, and writes the verdict. It also pulls the most
recent **prior** run for the same metric/source/target triple so the judge can
assess drift.

### Statistical methods

The evidence methods live in `methods.py`, registered by name in `METHODS`.
Each takes the two aligned series and returns one `DependencyMeasurement`
(`method`, `lag`, `score`, `direction`, `p_value`, `confidence`). A measurement
is just evidence — it never passes a verdict on its own.

- **`lagged_cross_correlation(source, target, max_lag)`** — sweeps lags in
  `[-max_lag, +max_lag]` and keeps the lag with the largest *absolute*
  cross-correlation. `score` is that correlation, `confidence` its absolute
  value, and `direction` follows the sign of the best lag: `source_leads` (lag
  > 0), `target_leads` (lag < 0), or `co_move` (lag = 0). It produces no
  `p_value` (`None`).
- **`granger(source, target, max_lag, min_points_factor, significance)`** — a
  Granger causality test of *source → target* across lags `1..max_lag`, picking
  the lag with the smallest F-test p-value. `score` is `-log10(p)`, `p_value` is
  that p (clamped to a `1e-12` floor so the significance threshold stays a real
  lever), and `confidence` is `1 - p`. `direction` is `source_leads` when
  `p < significance`, otherwise `inconclusive`. If the series is shorter than
  `min_points_factor * max_lag` the test is skipped and a neutral,
  `inconclusive` measurement is returned. The two tunables come from config:
  **`granger_min_points_factor`** (the minimum-points multiplier) and
  **`granger_significance`** (the p-value threshold).

### The LLM judge

The judge is a PydanticAI `Agent` (`agent.py`). `build_agent()` assembles it
from the `agent` config — the provider model object, the structured-output mode,
and a fixed system prompt that instructs the model to weigh agreement between
methods, Granger significance, and lag plausibility; to always include the
"correlation is not causation" caveat; to calibrate confidence down when methods
disagree; and, when prior evidence is supplied, to record what changed.

`judge_dependencies(measurements, meta, prior_measurements=None, agent=None)`
builds a prompt from the current (and optionally prior) evidence and returns a
`DependencyDecision` — a list of `DependencyRelation`, one per dependency, each
carrying `is_real` (bool), `confidence`, `explanation`, `caveats`, and a
`change_note` describing drift versus the previous run (empty on a first run).

Structured output is obtained according to `agent.output_mode`:

- **`native`** — JSON-schema native output (recommended for `ollama`).
- **`tool`** — tool-call output (recommended for the cloud providers).
- **`prompted`** — the schema is described in the prompt.

### What gets written, and where

`analyze_dependencies` writes to **two** contract tables:

- **`metric_dependency`** — one row per measurement (lag, score, direction,
  p_value, confidence, observation window). **Always written** when the analysis
  runs.
- **`dependency_explanation`** — one row per judged relation (`is_real`,
  `confidence`, `explanation`, `caveats`, `change_note`, model name). Written
  **only** when the judge produced a verdict.

The segment keys in `dependency_explanation` are taken from the job, not from
the LLM response — the model often strips the canonical `symbol=` prefix, which
would make the keys diverge from `metric_dependency` and break the join the
read tools rely on.

### Graceful degradation

When the configured LLM is unavailable, the judge raises **`LLMUnavailable`** — a
typed infrastructure error covering a missing credential, invalid config, or a
model/transport failure. `analyze_dependencies` catches it, logs an ERROR with a
full traceback, and skips the explanation step. The `metric_dependency` evidence
is still written; the result reports `explained=False` with a
`degradation_reason`. **This is not a run failure** — a numeric dependency
exists, only the LLM verdict is missing. Consumers see this through the
`explained` flag on the `get_dependencies` read tool. Programming bugs are *not*
masked as `LLMUnavailable`; they propagate as-is.

### The agent worker

`agent_worker.py` is a thin FastAPI boundary that lets the judge run as a
separate container (mirroring the TimesFM worker pattern):

- **`POST /judge`** — body `{measurements, meta, prior_measurements}`; returns a
  `DependencyDecision`. On `LLMUnavailable` it responds **503**.
- **`GET /health`** — liveness probe → `{"status": "ok"}`.

The worker builds its agent once at startup (`build_app()` is the uvicorn
factory), so a broken config fails fast. Inside the worker the judge is always
called with an **explicit** agent — never via `worker_url`, which would recurse.

The client side switches on **`agent.worker_url`**:

- **`null`** (default) — the judge runs **in-process** inside the deps job.
- **set** — `judge_dependencies` POSTs to the worker. Any non-200 (including the
  503 above) or transport error maps back to `LLMUnavailable`, i.e. the same
  explicit-degradation path. Leaving the worker **off is a normal state**, not
  an error — the deps job simply degrades (`explained=false`) and there are **no
  retries** for it.

An explicitly passed `agent=` (tests, local runs) always takes precedence over
`worker_url`.

### Feeding confirmed dependencies into forecasts

A confirmed dependency is a lead — exactly what an exogenous regressor needs to
be. A forecast job with **`use_dependencies: true`** auto-attaches every
`dependency_explanation` row with `is_real=1` and `direction='source_leads'` as
an XReg covariate, so the deps subsystem feeds the forecaster without listing
leaders by hand. See [norn-forecast](../forecast/README.md) and the
[covariates section in Jobs](../jobs.md#covariates-and-use_dependencies-xreg).

## Configuration

The dependency subsystem reads `agent.yml`. **LLM provider keys are never placed
in YAML** — they are env-only (see the provider table). Every field is also
overridable at runtime with the **`NORN_AGENT_<FIELD>`** env var (env beats
YAML), e.g. `NORN_AGENT_PROVIDER=openai-api` or `NORN_AGENT_WORKER_URL=…`.

### `agent.yml`

| Field                       | Type           | Description                                                                                                       |
| --------------------------- | -------------- | ----------------------------------------------------------------------------------------------------------------- |
| `provider`                  | string         | LLM provider: `ollama` \| `openai-api` \| `openai-oauth` \| `openrouter` \| `anthropic-api`.                      |
| `model`                     | string         | Model name for the chosen provider.                                                                               |
| `base_url`                  | string \| null | Provider endpoint. Required for `ollama` (explicit, no code fallback); set to `null` for cloud providers.         |
| `output_mode`               | string         | How structured output is obtained: `native` \| `tool` \| `prompted`.                                              |
| `max_lag`                   | int            | Maximum shift (lag), in series steps, probed when searching for dependencies. Default for unset job `max_lag`.    |
| `context_length`            | int            | History window length (points) fed to the analysis. Default for unset job `context_length`.                       |
| `methods`                   | list[string]   | Statistical methods, e.g. `[lagged_cross_correlation, granger]`. Default for unset job `methods`.                 |
| `granger_min_points_factor` | int            | Multiplier: minimum points for the Granger test = `factor * max_lag`.                                             |
| `granger_significance`      | float          | Granger p-value threshold; below this a dependency is `source_leads`, otherwise `inconclusive`.                   |
| `worker_url`                | string \| null | URL of the agent worker (the LLM judge as a separate HTTP service). `null` (default) = the judge runs in-process. |

### LLM providers

Five providers are supported. Each provider's secret comes from its own
environment variable (`ollama` needs no key, but does need a running daemon and a
pulled model):

| `provider`      | Secret env var            | `base_url`                                  | Recommended `output_mode` | Smoke-tested |
| --------------- | ------------------------- | ------------------------------------------- | ------------------------- | ------------ |
| `ollama`        | *(none)*                  | local URL, e.g. `http://localhost:11434/v1` | `native`                  | ✅ `gemma4:e2b` |
| `openai-api`    | `OPENAI_API_KEY`          | `null`                                      | `tool`                    | ✅ `gpt-4o-mini` |
| `openai-oauth`  | `NORN_OPENAI_OAUTH_TOKEN` | gateway URL (chat-completions)              | `tool`                    | ⚠️ bearer-only (see below) |
| `openrouter`    | `OPENROUTER_API_KEY`      | `null`                                      | `tool`                    | ✅ `openai/gpt-4o-mini` |
| `anthropic-api` | `ANTHROPIC_API_KEY`       | `null`                                      | `tool`                    | ✅ `claude-haiku-4-5` |

The four ✅ rows were verified end-to-end (real `norn deps` run, structured
verdict parsed) on the models shown. `openai-oauth` works only with an OAuth
bearer accepted by a chat-completions endpoint — see its section below.

The secret is read directly from the environment at model-build time; nothing is
called over the network while assembling the model object. A missing key surfaces
as `LLMUnavailable` at judge time (explicit degradation), not at config load.

> **ollama:** requires the Ollama daemon running and the chosen model pulled
> (`ollama pull <model>`). `base_url` must point at the Ollama OpenAI-compatible
> endpoint — there is no implicit fallback in code.

### Per-provider setup

Switching provider is always the same three env vars on top of `agent.yml`
(plus the provider's secret): `NORN_AGENT_PROVIDER`, `NORN_AGENT_MODEL`,
`NORN_AGENT_OUTPUT_MODE` — see [Configuration](../configuration.md) for where
to keep the secrets.

**`openai-api` (OpenAI platform key).** Create an API key at
*platform.openai.com → API keys* (`sk-...`). `base_url` stays `null` (the
client defaults to `api.openai.com`); set it only for an OpenAI-compatible
proxy/gateway.

```bash
export OPENAI_API_KEY=sk-...
export NORN_AGENT_PROVIDER=openai-api NORN_AGENT_MODEL=gpt-4o-mini NORN_AGENT_OUTPUT_MODE=tool
```

**`openrouter` (one key, many vendors).** Create a key at
*openrouter.ai → Keys* (`sk-or-...`). Models are addressed as
`<vendor>/<model>`; this is the quickest way to A/B different vendors without
new accounts. `base_url` is **ignored** for this provider (fixed endpoint).

```bash
export OPENROUTER_API_KEY=sk-or-...
export NORN_AGENT_PROVIDER=openrouter NORN_AGENT_MODEL=anthropic/claude-sonnet-4-5 NORN_AGENT_OUTPUT_MODE=tool
```

**`anthropic-api` (Claude direct).** Create a key at
*console.anthropic.com → API keys* (`sk-ant-...`). `base_url` is **ignored**
(fixed endpoint).

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export NORN_AGENT_PROVIDER=anthropic-api NORN_AGENT_MODEL=claude-sonnet-4-5 NORN_AGENT_OUTPUT_MODE=tool
```

For `ollama` see the note above (daemon + pulled model, explicit `base_url`,
no secret); for `openai-oauth` see the dedicated flow below.

**In Docker (compose):** the judge runs in the `agent` container (the scheduler
points `NORN_AGENT_WORKER_URL` at `http://agent:9400` by default). The split is
**YAML = settings, env = secrets**:

1. **Settings** — edit `config/agent.yml` (`provider`, `model`, `output_mode`,
   `base_url`) as usual: the services mount the repo's `config/` live, so
   `docker compose -f docker-compose.services.yml restart agent` applies it —
   no image rebuild.
2. **Secret** — copy `deploy/agent.env.example` → `deploy/agent.env`
   (gitignored) and uncomment the one key your provider needs; the `agent`
   service loads the file automatically. `ollama` needs no key — without the
   file the judge runs the default provider against the **host** daemon via
   `host.docker.internal:11434` (compose translates the YAML's localhost
   endpoint to the container-reachable address).

One catch for `openai-api`: also set `NORN_AGENT_BASE_URL=` (empty) in
`deploy/.env`, so the compose Ollama translation does not leak into the OpenAI
client (`${VAR-…}` semantics keep an explicitly-empty value empty).

### The `openai-oauth` provider (bearer token instead of an API key)

`openai-oauth` is identical to `openai-api` except the secret is read from
`NORN_OPENAI_OAUTH_TOKEN` and sent as a **bearer token** to an OpenAI-compatible
**chat-completions** endpoint. Use it when your access is an OAuth/bearer token
rather than a platform `sk-` key — e.g. an enterprise gateway or a proxy that
issues short-lived bearers:

```bash
export NORN_OPENAI_OAUTH_TOKEN=<bearer>
export NORN_AGENT_PROVIDER=openai-oauth NORN_AGENT_MODEL=<model> NORN_AGENT_OUTPUT_MODE=tool
# point at the endpoint that accepts your bearer (chat-completions API shape):
export NORN_AGENT_BASE_URL=https://<your-gateway>/v1
```

> **A ChatGPT / Codex-CLI OAuth token is NOT a drop-in here.** That token
> targets the Codex **Responses** backend (`chatgpt.com/backend-api/codex`),
> which this provider's chat-completions client does not speak, and the public
> `api.openai.com/v1` rejects it (`missing_scope: model.request`). Supporting
> it would require a separate Responses-API path with Codex-specific headers
> and model ids — out of scope for the platform. For OpenAI access use
> `openai-api` with a real `sk-` key; `openai-oauth` is for OAuth bearers that
> a chat-completions endpoint actually accepts.

Bearer tokens expire — refresh from your issuer and re-export on a 401.

## See also

- [norn-core](../core/README.md) — the shared config loader (`AgentSettings`)
  and the ClickHouse client this package builds on.
- [norn-forecast](../forecast/README.md) — how confirmed dependencies become
  XReg covariates via `use_dependencies`.
- [Jobs](../jobs.md#dependency-jobs) — authoring and running a `deps/*.yml` job.
- [Deployment](../deployment.md#services-scheduler-mcp-agent-worker) — running
  the agent worker (`:9400`) as a switchable service.
