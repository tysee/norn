"""
packages/core/src/norn_core/config.py

YAML-native типизированный config-слой платформы. По одному pydantic-settings классу
на секцию (database/forecast/agent/mcp), каждый читается из config/<section>.yml с
переопределением из env. Приоритет: env > YAML > дефолт поля.

Методы:
- get_settings() -> Settings — кэшированные настройки (читает NORN_CONFIG_DIR).
"""
from __future__ import annotations

import functools
import os
from pathlib import Path

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

    YAML_FILE: str = ""  # overridden per section

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
        yaml_src = YamlConfigSettingsSource(
            settings_cls, yaml_file=_config_dir() / cls.model_fields["YAML_FILE"].default
        )
        # priority: init kwargs (tests) > env > yaml > defaults
        return (init_settings, env_settings, yaml_src)


class DatabaseSettings(_YamlSection):
    model_config = SettingsConfigDict(env_prefix="NORN_DB_", env_nested_delimiter="__", extra="ignore")
    YAML_FILE: str = "database.yml"
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
    YAML_FILE: str = "forecast.yml"
    defaults: ForecastDefaults = ForecastDefaults()
    quantiles: list[float] = [0.1, 0.5, 0.9]
    timesfm: TimesFMSettings = TimesFMSettings()
    calibration: CalibrationSettings = CalibrationSettings()


class AgentSettings(_YamlSection):
    model_config = SettingsConfigDict(env_prefix="NORN_AGENT_", env_nested_delimiter="__", extra="ignore")
    YAML_FILE: str = "agent.yml"
    model: str = "anthropic:claude-sonnet-4-5"
    max_lag: int = 10
    context_length: int = 512
    methods: list[str] = ["lagged_cross_correlation", "granger"]
    granger_min_points_factor: int = 3


class McpSettings(_YamlSection):
    model_config = SettingsConfigDict(env_prefix="NORN_MCP_", env_nested_delimiter="__", extra="ignore")
    YAML_FILE: str = "mcp.yml"
    host: str = "127.0.0.1"
    port: int = 9200


class Settings(BaseModel):
    database: DatabaseSettings
    forecast: ForecastSettings
    agent: AgentSettings
    mcp: McpSettings


@functools.lru_cache(maxsize=1)
def _cached() -> Settings:
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
