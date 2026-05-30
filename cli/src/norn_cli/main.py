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

import subprocess
from pathlib import Path

import typer

from norn_agent.analyze import analyze_dependencies
from norn_agent.contract import DependencyJob
from norn_core.clickhouse import get_client
from norn_core.contract import ForecastJob
from norn_forecast.calibration import calibrate_job
from norn_forecast.runner import run_job
from norn_integration.schema import apply_schema

app = typer.Typer(help="norn — vendor-neutral forecasting layer")

COMPOSE = Path(__file__).resolve().parents[3] / "deploy" / "docker-compose.yml"


@app.command("schema-apply")
def schema_apply() -> None:
    """Apply the forecast-contract schema to ClickHouse (idempotent)."""
    client = get_client()
    apply_schema(client)
    typer.echo("schema applied")


@app.command()
def forecast(job_path: str = typer.Argument(..., help="path to a forecast-job YAML")) -> None:
    """Run a forecast job: extract -> forecast -> write contract rows."""
    # --- читаем декларативный job из YAML ---
    job = ForecastJob.from_yaml(job_path)
    # --- подключаемся к хранилищу и гарантируем актуальность схемы ---
    client = get_client()
    apply_schema(client)
    # --- запускаем прогон и печатаем идентификатор запуска ---
    run_id = run_job(job, client=client)
    typer.echo(f"run_id={run_id}")


@app.command()
def calibrate(job_path: str = typer.Argument(..., help="path to a forecast-job YAML")) -> None:
    """Rolling-origin calibration: writes coverage/wape/mape/bias to forecast_segment."""
    # --- читаем тот же job-контракт, что и для прогноза ---
    job = ForecastJob.from_yaml(job_path)
    # --- подключаемся к хранилищу и гарантируем актуальность схемы ---
    client = get_client()
    apply_schema(client)
    # --- прогоняем rolling-origin калибровку и печатаем run_id ---
    run_id = calibrate_job(job, client=client)
    typer.echo(f"calibration run_id={run_id}")


@app.command()
def deps(job_path: str = typer.Argument(..., help="path to a dependency-job YAML")) -> None:
    """Analyze lead/lag dependencies and write evidence + the agent's explanation."""
    # --- читаем декларативный dependency-job из YAML ---
    job = DependencyJob.from_yaml(job_path)
    # --- подключаемся к хранилищу и гарантируем актуальность схемы ---
    client = get_client()
    apply_schema(client)
    # --- считаем зависимости, пишем evidence и печатаем run_id ---
    run_id = analyze_dependencies(job, client=client)
    typer.echo(f"deps run_id={run_id}")


@app.command()
def mcp() -> None:
    """Run the MCP server (streamable-http) so agents can query forecasts."""
    from norn_forecast.mcp_server import build_server

    build_server().run(transport="streamable-http")


@app.command()
def up() -> None:
    """Bring up the local sidecar (ClickHouse) in Docker and apply the schema."""
    subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), "up", "-d", "clickhouse"], check=True
    )
    typer.echo("clickhouse up; run `norn schema-apply` once it is healthy")


if __name__ == "__main__":
    app()
