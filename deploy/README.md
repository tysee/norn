# Local Lightdash + ClickHouse stack

The two forms Lightdash shows on first run map to two things:

| Form (UI)              | What it is                                   | Where it comes from here              |
| ---------------------- | -------------------------------------------- | ------------------------------------- |
| **Warehouse connection** | where the data tables live                 | ClickHouse (`profiles.yml`)           |
| **dbt connection**       | where the model/metric definitions live    | the dbt project, pushed via the CLI   |

> The yellow logo on the first screen is **ClickHouse**, not Postgres. Postgres
> here is only Lightdash's own metadata DB (`lightdash-db`).

You don't fill either form by hand. `bootstrap-lightdash.sh` registers the admin,
mints a token, runs dbt, and calls `lightdash deploy --create`, which reads
`profiles.yml` and pushes the warehouse connection **and** a compiled manifest.

This directory is the **generic platform stack** — no domain (crypto, etc.)
specifics. Domain policy (project name, which dbt project to publish, warehouse
creds) lives in each instance's env-file: `instances/<domain>/deploy/.env`.

## Local — generic demo (one command, fully headless)

```bash
cd deploy
cp .env.example .env          # generic demo defaults

docker compose up -d          # clickhouse + lightdash + deps
docker compose --profile setup run --rm lightdash-init
# → builds a one-shot image, registers admin, dbt run, creates the demo project
```

Open <http://localhost:8080>, log in with `LD_ADMIN_EMAIL` / `LD_ADMIN_PASSWORD`.
Re-running `lightdash-init` is safe: it logs in instead of re-registering and
re-deploys instead of re-creating.

## Local — a domain instance (e.g. crypto)

Don't edit `deploy/.env`. Point the stack at the instance's env-file instead, so
the platform stays domain-neutral. Run from the **repo root**:

```bash
cp instances/crypto/deploy/.env.example instances/crypto/deploy/.env

docker compose -f deploy/docker-compose.yml up -d
docker compose -f deploy/docker-compose.yml \
  --env-file instances/crypto/deploy/.env --profile setup run --rm lightdash-init
```

`dbt run` needs the source tables to exist, so run the instance's ingest
(`uv run crypto update`) first or that step fails with "table not found".

## Production — when dbt already exists for the warehouse

Don't run this init container against prod. Instead, on the prod Lightdash:

1. **Warehouse connection** form → ClickHouse: host, user/password, port `8443`
   (or `9440` native), **SSL on**, database `norn`.
2. **dbt connection** form → GitHub, branch `main`,
   **Project directory path `/instances/crypto/dbt`** (not `/`), target `prod`,
   schema `norn`.
3. Add a `prod` output to `instances/crypto/dbt/profiles.yml` (ClickHouse via
   `env_var`, `secure: true`) and a cron that runs `dbt run` against prod — and,
   if you keep the CLI flow, `lightdash deploy` — so the marts stay fresh.

Lightdash never computes data itself; it reads already-materialized tables, so a
scheduled `dbt run` is mandatory in prod.

## Files

- `docker-compose.yml` — clickhouse, lightdash (+ pg, minio, headless browser),
  and the opt-in `lightdash-init` service (compose profile `setup`).
- `bootstrap-lightdash.sh` — the headless API + CLI flow.
- `lightdash-init.Dockerfile` — node (lightdash CLI) + dbt-clickhouse + jq.
- `.env.example` — generic platform knobs (demo defaults, no domain).

Domain specifics (e.g. crypto) live in `instances/<domain>/deploy/.env` and the
instance's own dbt project + `schema.yml`.
