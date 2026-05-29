"""
cli/src/norn_cli/main.py

CLI norn (typer): поднять локальный сайдкар, применить схему, прогнать прогноз
и калибровку.

Команды:
- up — поднять ClickHouse в Docker.
- schema-apply — применить контракт-схему.
- forecast <job> — прогон прогноза.
- calibrate <job> — rolling-origin калибровка.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import typer

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
    job = ForecastJob.from_yaml(job_path)
    client = get_client()
    apply_schema(client)
    run_id = run_job(job, client=client)
    typer.echo(f"run_id={run_id}")


@app.command()
def calibrate(job_path: str = typer.Argument(..., help="path to a forecast-job YAML")) -> None:
    """Rolling-origin calibration: writes coverage/wape/mape/bias to forecast_segment."""
    job = ForecastJob.from_yaml(job_path)
    client = get_client()
    apply_schema(client)
    run_id = calibrate_job(job, client=client)
    typer.echo(f"calibration run_id={run_id}")


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
