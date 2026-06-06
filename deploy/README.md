# deploy/ — the platform runtime layer

This directory is the **generic platform stack** — images, compose files, and
the Lightdash bootstrap. Nothing domain-specific lives here; instance policy
(what to publish, what to schedule) lives in each instance's own `deploy/`.

## The four layers (who owns what)

| Layer | Lives in | Owns | Must never contain |
| --- | --- | --- | --- |
| **Platform runtime config** | `config/*.yml` (any dir via `NORN_CONFIG_DIR`) | how norn behaves: DB connection, forecast defaults, LLM provider, service ports | secrets (env-only), domain specifics |
| **Platform deploy** (this dir) | `deploy/` | how norn runs: infra + services compose, the four images, Lightdash bootstrap, the generic demo dbt project | instance/domain policy |
| **Instance deploy** | `instances/<i>/deploy/` | the instance's operational policy: scheduler manifest `jobs.yml`, `crontab.sample`, Lightdash publish env-file | platform mechanics |
| **Instance data layer** | `instances/<i>/{dbt,forecasts,config}` | marts, forecast/deps job YAMLs, optionally a full `NORN_CONFIG_DIR` (see `instances/example/config/`) | — |

## Env-file matrix (which `.env` does what)

| File | Mechanism — how it reaches a process | Purpose | In git |
| --- | --- | --- | --- |
| `deploy/.env` | compose **interpolation**: substitutes `${VAR}` inside the compose YAML; vars do **not** enter containers unless a service's `environment:` maps them | stack knobs: ClickHouse creds, `LIGHTDASH_SECRET` (keep constant!), admin bootstrap values, default demo publish policy, `COMPOSE_IGNORE_ORPHANS` | only `.env.example` |
| `deploy/agent.env` | **`env_file`** on the `agent` service: injected **wholesale into that one container** (optional — absent file is fine) | the LLM judge's provider **secret only** (`*_API_KEY` / OAuth token); the provider/model/output_mode **settings** live in `config/agent.yml`, mounted live into the container | only `agent.env.example` |
| `instances/<i>/deploy/.env` | passed explicitly to one command: `lightdash-init` via `--env-file` | which Lightdash project name + dbt dir to publish for THIS instance (`LD_PROJECT_NAME`, `DBT_PROJECT_HOST_DIR`) | only `.env.example` |
| shell env / k8s Secrets | ordinary process environment | secrets and ad-hoc `NORN_<SECTION>_<FIELD>` overrides for the `norn` CLI and services | never |

Rule of thumb: **`deploy/.env` = the stack, `instances/<i>/deploy/.env` = what to
publish, `agent.env` = who judges.**

> **Why is `agent.env` a separate file and not part of `deploy/.env`?**
> Different mechanisms. `deploy/.env` only feeds `${...}` interpolation — adding
> `OPENAI_API_KEY` there would go nowhere unless every provider var were also
> hardcoded into the compose YAML (which would clobber the image's config
> defaults with empty strings when unset). Loading all of `deploy/.env` into the
> agent container instead would hand the LLM judge the Lightdash admin password
> and stack secrets it has no business seeing. A dedicated `env_file` gives the
> judge exactly its own variables — nothing more. So: the provider **key** →
> `cp agent.env.example agent.env`; the provider **settings** →
> `config/agent.yml` (mounted live — restart the service to apply); everything
> stack-wide → `.env`.

**Settings vs secrets.** The norn services (`scheduler`, `mcp`, `agent`) mount
the repo's `config/` **live** (read-only), overriding the copy baked into the
image — so changing any `config/*.yml` needs only a `restart` of the service,
never a rebuild, and env files never carry settings: YAML = settings, env =
secrets (plus infra translations like the container-reachable Ollama URL).

Where to keep secrets in general is covered in
[Configuration](../docs/guide/configuration.md#where-to-keep-them-so-they-never-reach-the-repo).

## dbt: three projects, three roles

| Project | Role |
| --- | --- |
| `deploy/dbt` (`norn_demo`) | the **generic demo** the default bootstrap publishes: `raw_metric` → `mart_metric`, `actual_vs_forecast`. Zero domain content; exists so a fresh checkout gets a working Lightdash project with no data of its own. |
| `instances/ett/dbt` | the **worked example** with real data (ETT marts: `mart_metric`, `fct_ot`, calibration views). |
| `instances/example/dbt` | the **skeleton to copy** for your own instance (templates shaping the platform contract). |

`logs/`, `target/`, `.user.yml` inside any dbt dir are run artifacts — gitignored, safe to delete.

## Files in this directory

| File | Purpose |
| --- | --- |
| `docker-compose.yml` | **infra**: ClickHouse + the Lightdash stack (server, Postgres, browserless, MinIO) + the opt-in `lightdash-init` (profile `setup`) |
| `docker-compose.services.yml` | **norn services** (opt-in profiles): `timesfm` :9100, `scheduler` :9300, `mcp` :9200, `agent` :9400 — split from infra so `down` on services can never remove ClickHouse |
| `norn.Dockerfile` | the light platform image (`norn:local`) — role chosen by command (`norn scheduler` / `norn mcp` / ad-hoc CLI) |
| `agent.Dockerfile` | the LLM judge image (`norn-agent:local`) — pydantic-ai + httpx, no torch |
| `timesfm.Dockerfile` + `timesfm-requirements.txt` | the self-contained TimesFM inference worker (`norn-timesfm:local`) — the only torch/jax image |
| `lightdash-init.Dockerfile` | one-shot bootstrap image: lightdash CLI + dbt-clickhouse + jq |
| `bootstrap-lightdash.sh` | the headless bootstrap: health → admin → org → PAT → `dbt run` → `lightdash deploy --create`; idempotent |
| `.env.example` | template for `deploy/.env` (see the matrix above) |
| `agent.env.example` | template for `deploy/agent.env` — one block per LLM provider |
| `dbt/` | the `norn_demo` generic dbt project (see above) |

Two compose rules: bring infra up **first**, and **never pass
`--remove-orphans`** (each file sees the other's containers as orphans;
`COMPOSE_IGNORE_ORPHANS=true` silences the warning).

## Quickstart — generic demo (one command, fully headless)

```bash
cd deploy
cp .env.example .env          # generic demo defaults

docker compose up -d          # clickhouse + lightdash + deps
docker compose --profile setup run --rm lightdash-init
```

Open <http://localhost:8080>, log in with `LD_ADMIN_EMAIL` / `LD_ADMIN_PASSWORD`.
The two Lightdash setup forms (warehouse connection, dbt connection) are filled
by the bootstrap via API + `lightdash deploy` — never by hand. Re-running
`lightdash-init` is safe: it logs in instead of re-registering and re-deploys
instead of re-creating.

## Quickstart — an instance (the ETT example)

Don't edit `deploy/.env` for this. Point the bootstrap at the instance's
env-file instead, so the platform stays domain-neutral. From the **repo root**:

```bash
cp instances/ett/deploy/.env.example instances/ett/deploy/.env

docker compose -f deploy/docker-compose.yml up -d
docker compose -f deploy/docker-compose.yml \
  --env-file instances/ett/deploy/.env --profile setup run --rm lightdash-init
```

`dbt run` needs the source tables to exist, so run the instance's ingest first
(`uv run ett backfill` from `instances/ett/`) or that step fails with "table
not found".

The norn services (scheduler / mcp / agent / timesfm) are started separately
from `docker-compose.services.yml` — see
[Deployment](../docs/guide/deployment.md#services-scheduler-mcp-agent-worker)
for profiles, the `NORN_JOBS_DIR` mount, and the service env matrix.

## Production — when dbt already exists for the warehouse

Don't run the init container against prod. On the prod Lightdash fill the two
forms once: **warehouse connection** → your ClickHouse (TLS port, `secure` on);
**dbt connection** → your repo, project directory = the instance's dbt path
(e.g. `/instances/<i>/dbt`). Add a `prod` output to the instance's
`profiles.yml` (creds via `env_var`) and schedule `dbt run` — Lightdash never
computes data itself, it reads already-materialized tables.

## See also

- [Deployment guide](../docs/guide/deployment.md) — services, failure matrix, cloud/k8s.
- [Lightdash integration](../docs/integration/lightdash.md) — the stack in detail + driving charts via MCP/LLM.
- [Configuration](../docs/guide/configuration.md) — config layers, env overrides, secrets.
