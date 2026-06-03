"""
cli/src/norn_cli/main.py

Единая точка входа в платформу norn через командную строку (на базе typer).
Связывает воедино подсистемы платформы — локальный сайдкар-хранилище
(ClickHouse в Docker), применение контракт-схемы, запуск прогнозов и их
калибровки, анализ зависимостей рядов и поднятие MCP-сервера — давая
оператору один CLI вместо вызова каждой подсистемы по отдельности.

Объект:
- app — корневое typer-приложение, собирающее все команды.

Команды (typer):
- schema_apply — идемпотентно применить контракт-схему к ClickHouse.
- forecast — прогон job: extract -> forecast -> запись строк контракта.
- calibrate — rolling-origin калибровка (coverage/wape/mape/bias).
- deps — анализ lead/lag зависимостей + объяснение агента.
- mcp — поднять MCP-сервер (streamable-http) для запросов агентов.
- up — поднять локальный сайдкар (ClickHouse) в Docker.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Annotated

import typer

from norn_agent.analyze import analyze_dependencies
from norn_agent.contract import DependencyJob
from norn_core.clickhouse import get_client
from norn_core.config import get_settings
from norn_core.contract import ForecastJob
from norn_forecast.calibration import calibrate_job
from norn_forecast.runner import run_job
from norn_integration.schema import apply_schema, prepare_schema, schema_sql

app = typer.Typer(help="norn — vendor-neutral forecasting layer")

# Default local-dev compose file (source checkout). Overridable via NORN_COMPOSE_FILE
# so `up` is not tied to a fixed repo layout (e.g. when norn-cli is pip-installed).
DEFAULT_COMPOSE = Path(__file__).resolve().parents[3] / "deploy" / "docker-compose.yml"


@app.command("schema-apply")
def schema_apply() -> None:
    """Apply the forecast-contract schema to ClickHouse (idempotent). Honors database.manage_schema."""
    if not get_settings().database.manage_schema:
        typer.secho(
            "database.manage_schema=false; norn won't run DDL. "
            "Use `norn print-schema` + your own dbt/migrations to create the contract tables.",
            fg=typer.colors.RED, err=True,
        )
        raise typer.Exit(1)
    # --- one-shot CLI: own the connection pool and release it on exit ---
    client = get_client()
    try:
        apply_schema(client, get_settings().forecast.retention_months)
        typer.echo("schema applied")
    finally:
        client.close()


@app.command("print-schema")
def print_schema() -> None:
    """Print the canonical contract DDL (feed into your dbt/migrations when manage_schema=false)."""
    typer.echo(schema_sql(get_settings().forecast.retention_months))


@app.command()
def forecast(
    job_path: Annotated[str, typer.Argument(help="path to a forecast-job YAML")],
) -> None:
    """Run a forecast job: extract -> forecast -> write contract rows."""
    # --- читаем декларативный job из YAML ---
    job = ForecastJob.from_yaml(job_path)
    # --- подключаемся к хранилищу и гарантируем актуальность схемы ---
    # one-shot CLI: own the connection pool and release it on exit
    client = get_client()
    try:
        s = get_settings()
        prepare_schema(client, s.database.manage_schema, s.forecast.retention_months)
        # --- запускаем прогон и печатаем идентификатор запуска ---
        run_id = run_job(job, client=client)
        typer.echo(f"run_id={run_id}")
    finally:
        client.close()


@app.command()
def calibrate(
    job_path: Annotated[str, typer.Argument(help="path to a forecast-job YAML")],
) -> None:
    """Rolling-origin calibration: writes coverage/wape/mape/bias to forecast_segment."""
    # --- читаем тот же job-контракт, что и для прогноза ---
    job = ForecastJob.from_yaml(job_path)
    # --- подключаемся к хранилищу и гарантируем актуальность схемы ---
    # one-shot CLI: own the connection pool and release it on exit
    client = get_client()
    try:
        s = get_settings()
        prepare_schema(client, s.database.manage_schema, s.forecast.retention_months)
        # --- прогоняем rolling-origin калибровку и печатаем run_id ---
        run_id = calibrate_job(job, client=client)
        typer.echo(f"calibration run_id={run_id}")
    finally:
        client.close()


@app.command()
def deps(
    job_path: Annotated[str, typer.Argument(help="path to a dependency-job YAML")],
) -> None:
    """Analyze lead/lag dependencies and write evidence + the agent's explanation."""
    # --- читаем декларативный dependency-job из YAML ---
    job = DependencyJob.from_yaml(job_path)
    # --- подключаемся к хранилищу и гарантируем актуальность схемы ---
    # one-shot CLI: own the connection pool and release it on exit
    client = get_client()
    try:
        s = get_settings()
        # Прогресс: судья-LLM работает минуты (локальная Ollama) — без этих строк
        # прогон выглядит зависшим. basicConfig поднимает INFO-вехи analyze в stderr
        # (no-op, если логирование уже настроено хостом).
        import logging

        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
        typer.echo(
            f"deps: {job.source_segment} -> {job.target_segment} metric={job.metric} "
            f"(LLM judge: {s.agent.provider}/{s.agent.model})"
        )
        prepare_schema(client, s.database.manage_schema, s.forecast.retention_months)
        # --- считаем зависимости, пишем evidence и печатаем run_id ---
        res = analyze_dependencies(job, client=client)
        typer.echo(f"deps run_id={res.run_id}")
        if not res.explained:
            typer.secho(
                f"⚠ LLM explanation skipped: {res.degradation_reason}",
                fg=typer.colors.YELLOW,
                err=True,
            )
    finally:
        client.close()


@app.command()
def mcp() -> None:
    """Run the MCP server (streamable-http) so agents can query forecasts."""
    from pydantic import ValidationError

    from norn_forecast.mcp_server import build_server

    # Конфиг-ошибки (нет NORN_DB_PASSWORD и т.п.) — это операторская проблема,
    # а не баг: печатаем, какие env/поля не заданы, вместо сырого traceback.
    try:
        server = build_server()
    except ValidationError as e:
        missing = ", ".join(
            str(err["loc"][0]) for err in e.errors() if err["type"] == "missing"
        ) or str(e)
        typer.secho(
            f"config error: missing required settings: {missing}. "
            "Secrets come from env (e.g. NORN_DB_PASSWORD for the ClickHouse password); "
            "see config/*.yml for the non-secret fields.",
            fg=typer.colors.RED, err=True,
        )
        raise typer.Exit(1) from e
    s = get_settings().mcp
    typer.echo(f"norn MCP server on http://{s.host}:{s.port}/mcp (streamable-http)")
    server.run(transport="streamable-http")


@app.command()
def up() -> None:
    """[local-dev only] Bring up a local ClickHouse sidecar via Docker Compose.

    This is a developer convenience, not a platform requirement: the platform
    connects to ClickHouse purely via config/env (NORN_CLICKHOUSE_URL or the
    NORN_DB_* settings). For cloud/k8s, point those at your managed ClickHouse
    and skip `norn up`. Override the compose file with NORN_COMPOSE_FILE.
    """
    # --- pre-flight: this command needs local Docker; fail clearly, not cryptically ---
    if shutil.which("docker") is None:
        typer.secho(
            "`norn up` is a local-dev convenience and needs Docker installed. "
            "For cloud/k8s, set NORN_CLICKHOUSE_URL to your managed ClickHouse "
            "and skip `norn up`.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    compose = Path(os.environ.get("NORN_COMPOSE_FILE", DEFAULT_COMPOSE))
    if not compose.is_file():
        typer.secho(
            f"compose file not found: {compose}. Set NORN_COMPOSE_FILE to your "
            "docker-compose.yml (local-dev only; cloud/k8s should skip `norn up`).",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    subprocess.run(
        ["docker", "compose", "-f", str(compose), "up", "-d", "clickhouse"], check=True
    )
    typer.echo("clickhouse up; run `norn schema-apply` once it is healthy")


if __name__ == "__main__":
    app()
