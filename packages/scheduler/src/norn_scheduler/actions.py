"""
packages/scheduler/src/norn_scheduler/actions.py

Один запуск одной манифест-джобы — общая единица для cron-тика, ретраев и
ручного /trigger. Повторяет one-shot жизненный цикл CLI: открыть клиент,
prepare_schema, выполнить действие, закрыть клиент. Аудит пишут сами действия
(run_job -> forecast_run и т.д.) — здесь только диспетчеризация.
"""
from __future__ import annotations

from norn_agent.analyze import analyze_dependencies
from norn_agent.contract import DependencyJob
from norn_core.clickhouse import get_client
from norn_core.config import get_settings
from norn_core.contract import ForecastJob
from norn_forecast.calibration import calibrate_job
from norn_forecast.runner import run_job
from norn_integration.schema import prepare_schema

from norn_scheduler.manifest import ManifestJob


def run_action(entry: ManifestJob) -> str:
    """Execute one manifest job once; returns the run_id written by the action."""
    client = get_client()
    try:
        s = get_settings()
        prepare_schema(client, s.database.manage_schema, s.forecast.retention_months)
        if entry.action == "forecast":
            return run_job(ForecastJob.from_yaml(entry.job), client=client)
        if entry.action == "calibrate":
            return calibrate_job(ForecastJob.from_yaml(entry.job), client=client)
        # manifest validation guarantees the only remaining action is "deps"
        return analyze_dependencies(DependencyJob.from_yaml(entry.job), client=client).run_id
    finally:
        client.close()
