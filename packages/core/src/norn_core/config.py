"""
packages/core/src/norn_core/config.py

YAML-native типизированный config-слой платформы norn. Один pydantic-settings класс
на секцию (database/forecast/agent/mcp); каждый читает свой config/<section>.yml и
позволяет переопределять значения через переменные окружения. Это единый источник
настроек для всех сервисов платформы: воркеры и агент не парсят файлы напрямую, а
получают валидированные типизированные объекты с предсказуемым приоритетом источников.

Приоритет источников: init-аргументы (тесты) > env > YAML-файл > дефолт поля.

Классы/методы:
- _YamlSection — базовый класс секции; настраивает порядок источников (env > yaml > defaults).
- DatabaseSettings — подключение к ClickHouse (host/port/user/password/database/secure + DSN-override).
- ForecastDefaults / TimesFMSettings / CalibrationSettings — вложенные блоки настроек прогноза.
- ForecastSettings — секция прогноза (дефолты, квантили, параметры TimesFM, калибровка).
- AgentSettings — секция агента (модель, лаги, методы анализа зависимостей).
- McpSettings — секция MCP-сервера (host/port).
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
    """Base: env > config/<YAML_FILE> > field default."""

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
        yaml_src = YamlConfigSettingsSource(
            settings_cls, yaml_file=_config_dir() / cls.YAML_FILE
        )
        # --- порядок = приоритет: init kwargs (tests) > env > yaml > defaults ---
        return (init_settings, env_settings, yaml_src)


class DatabaseSettings(_YamlSection):
    model_config = SettingsConfigDict(env_prefix="NORN_DB_", env_nested_delimiter="__", extra="ignore")
    YAML_FILE: ClassVar[str] = "database.yml"
    host: str = "localhost"
    port: int = 8123
    user: str = "norn"
    password: str = "norn"
    database: str = "norn"
    secure: bool = False
    # full-DSN override (back-compat): env NORN_CLICKHOUSE_URL
    dsn: str | None = Field(default=None, validation_alias=AliasChoices("NORN_CLICKHOUSE_URL", "dsn"))


class ForecastDefaults(BaseModel):
    horizon: int = 30
    context_length: int = 512
    seasonality: int = 7


class TimesFMSettings(BaseModel):
    worker_url: str = "http://localhost:9100"
    max_context: int = 1024
    max_horizon: int = 1024


class CalibrationSettings(BaseModel):
    n_cutoffs: int = 3


class ForecastSettings(_YamlSection):
    model_config = SettingsConfigDict(env_prefix="NORN_FORECAST_", env_nested_delimiter="__", extra="ignore")
    YAML_FILE: ClassVar[str] = "forecast.yml"
    defaults: ForecastDefaults = ForecastDefaults()
    quantiles: list[float] = [0.1, 0.5, 0.9]
    timesfm: TimesFMSettings = TimesFMSettings()
    calibration: CalibrationSettings = CalibrationSettings()


class AgentSettings(_YamlSection):
    model_config = SettingsConfigDict(env_prefix="NORN_AGENT_", env_nested_delimiter="__", extra="ignore")
    YAML_FILE: ClassVar[str] = "agent.yml"
    model: str = "anthropic:claude-sonnet-4-5"
    max_lag: int = 10
    context_length: int = 512
    methods: list[str] = ["lagged_cross_correlation", "granger"]
    granger_min_points_factor: int = 3
    granger_significance: float = 0.05


class McpSettings(_YamlSection):
    model_config = SettingsConfigDict(env_prefix="NORN_MCP_", env_nested_delimiter="__", extra="ignore")
    YAML_FILE: ClassVar[str] = "mcp.yml"
    host: str = "127.0.0.1"
    port: int = 9200


class Settings(BaseModel):
    database: DatabaseSettings
    forecast: ForecastSettings
    agent: AgentSettings
    mcp: McpSettings


@functools.lru_cache(maxsize=1)
def _cached() -> Settings:
    # --- сборка агрегата: каждая секция читает свой YAML + env при инстанцировании ---
    return Settings(
        database=DatabaseSettings(),
        forecast=ForecastSettings(),
        agent=AgentSettings(),
        mcp=McpSettings(),
    )


def get_settings(refresh: bool = False) -> Settings:
    if refresh:
        _cached.cache_clear()
    return _cached()
