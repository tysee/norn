"""
packages/core/src/norn_core/config.py

YAML-native типизированный config-слой платформы norn. Один pydantic-settings класс
на секцию (database/forecast/agent/mcp); каждый читает свой config/<section>.yml и
позволяет переопределять значения через переменные окружения. Это единый источник
настроек для всех сервисов платформы: воркеры и агент не парсят файлы напрямую, а
получают валидированные типизированные объекты с предсказуемым приоритетом источников.

Приоритет источников: init-аргументы (тесты) > env > YAML-файл. Python-дефолтов полей
больше нет — YAML/env являются единственными источниками обязательных значений; отсутствие
ключа во всех источниках → явный ValidationError (а не тихая подстановка дефолта).

Классы/методы:
- _YamlSection — базовый класс секции; настраивает порядок источников (env > yaml).
- DatabaseSettings — подключение к ClickHouse (host/port/user/password/database/secure + DSN-override).
- ForecastDefaults / TimesFMSettings / CalibrationSettings — вложенные блоки настроек прогноза.
- ForecastSettings — секция прогноза (дефолты, квантили, параметры TimesFM, калибровка).
- AgentSettings — секция агента (модель, лаги, методы анализа зависимостей).
- McpSettings — секция MCP-сервера (host/port).
- SchedulerSettings — секция встроенного шедулера (host/port + политика ретраев/misfire).
- Settings — агрегат всех секций.
- get_settings(refresh=False) -> Settings — кэшированные настройки (читает NORN_CONFIG_DIR; refresh сбрасывает кэш).
"""
from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import ClassVar

from pydantic import AliasChoices, BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)


def _config_dir() -> Path:
    return Path(os.environ.get("NORN_CONFIG_DIR", "config"))


class _YamlSection(BaseSettings):
    """Base: env > config/<YAML_FILE> (no field defaults — required keys must exist)."""

    YAML_FILE: ClassVar[str] = ""  # overridden per section

    model_config = SettingsConfigDict(env_nested_delimiter="__", extra="ignore")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ):
        # --- источник YAML: файл секции из каталога NORN_CONFIG_DIR ---
        # Явный, диагностируемый отказ, если конфиг-файл не найден (напр. в
        # контейнере/k8s, где cwd != корень репо): иначе pydantic молча игнорирует
        # отсутствующий YAML и падает обобщённым "field required".
        yaml_path = _config_dir() / cls.YAML_FILE
        if not yaml_path.is_file():
            raise FileNotFoundError(
                f"norn config file not found: {yaml_path}. Set NORN_CONFIG_DIR to the "
                f"directory containing {cls.YAML_FILE} (and the other section YAMLs). "
                f"cwd={Path.cwd()}."
            )
        yaml_src = YamlConfigSettingsSource(settings_cls, yaml_file=yaml_path)
        # --- порядок = приоритет: init kwargs (tests) > env > yaml (без дефолтов полей) ---
        return (init_settings, env_settings, yaml_src)


class DatabaseSettings(_YamlSection):
    model_config = SettingsConfigDict(env_prefix="NORN_DB_", env_nested_delimiter="__", extra="ignore")
    YAML_FILE: ClassVar[str] = "database.yml"
    host: str
    port: int
    user: str
    database: str
    secure: bool
    manage_schema: bool   # true: norn creates contract tables; false: INSERT-only, DDL is external
    # секрет: ТОЛЬКО из env NORN_DB_PASSWORD (не в YAML, без дефолта)
    password: str = Field(validation_alias=AliasChoices("NORN_DB_PASSWORD", "password"))
    # единственный опциональный override (env NORN_CLICKHOUSE_URL): None = override не задан
    dsn: str | None = Field(default=None, validation_alias=AliasChoices("NORN_CLICKHOUSE_URL", "dsn"))


class ForecastDefaults(BaseModel):
    horizon: int
    context_length: int
    seasonality: int


class TimesFMSettings(BaseModel):
    worker_url: str
    max_context: int
    max_horizon: int


class CalibrationSettings(BaseModel):
    n_cutoffs: int


class CovariatesSettings(BaseModel):
    horizon_policy: str   # strict | ffill
    xreg_mode: str


class ForecastSettings(_YamlSection):
    model_config = SettingsConfigDict(env_prefix="NORN_FORECAST_", env_nested_delimiter="__", extra="ignore")
    YAML_FILE: ClassVar[str] = "forecast.yml"
    defaults: ForecastDefaults
    quantiles: list[float]
    timesfm: TimesFMSettings
    calibration: CalibrationSettings
    covariates: CovariatesSettings
    retention_months: int   # TTL for contract tables, in months; 0 = no TTL


class AgentSettings(_YamlSection):
    model_config = SettingsConfigDict(env_prefix="NORN_AGENT_", env_nested_delimiter="__", extra="ignore")
    YAML_FILE: ClassVar[str] = "agent.yml"
    provider: str               # ollama | openai-api | openai-oauth | openrouter | anthropic-api
    model: str
    base_url: str | None        # ollama: обязателен URL; cloud: null. Нет неявного фолбека.
    output_mode: str            # native | tool | prompted
    max_lag: int
    context_length: int
    methods: list[str]
    granger_min_points_factor: int
    granger_significance: float
    # null = LLM judge runs in-process (current behavior); URL = call the agent-worker
    worker_url: str | None = None


class McpSettings(_YamlSection):
    model_config = SettingsConfigDict(env_prefix="NORN_MCP_", env_nested_delimiter="__", extra="ignore")
    YAML_FILE: ClassVar[str] = "mcp.yml"
    host: str
    port: int


class SchedulerSettings(_YamlSection):
    model_config = SettingsConfigDict(env_prefix="NORN_SCHEDULER_", env_nested_delimiter="__", extra="ignore")
    YAML_FILE: ClassVar[str] = "scheduler.yml"
    host: str
    port: int
    retries: int                  # default retry attempts per job (manifest may override)
    retry_base_seconds: int       # exponential backoff base: base * 2**attempt
    misfire_grace_seconds: int    # how late a missed cron tick may still fire once


class Settings(BaseModel):
    database: DatabaseSettings
    forecast: ForecastSettings
    agent: AgentSettings
    mcp: McpSettings
    scheduler: SchedulerSettings


@functools.lru_cache(maxsize=1)
def _cached() -> Settings:
    # --- сборка агрегата: каждая секция читает свой YAML + env при инстанцировании ---
    return Settings(
        database=DatabaseSettings(),
        forecast=ForecastSettings(),
        agent=AgentSettings(),
        mcp=McpSettings(),
        scheduler=SchedulerSettings(),
    )


def get_settings(refresh: bool = False) -> Settings:
    if refresh:
        _cached.cache_clear()
    return _cached()
