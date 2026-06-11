"""
packages/core/src/norn_core/config.py

YAML-native typed config layer of the norn platform. One pydantic-settings class
per section (database/forecast/agent/mcp); each reads its own config/<section>.yml and
allows overriding values via environment variables. This is the single source of
settings for all platform services: workers and the agent do not parse files directly,
they receive validated typed objects with a predictable source priority.

Source priority: init-arguments (tests) > env > YAML-file. Python field defaults are
gone — YAML/env are the only sources of required values; a missing key in all
sources → an explicit ValidationError (not a silent default substitution).

Classes/methods:
- _YamlSection — base section class; sets up the source order (env > yaml).
- DatabaseSettings — ClickHouse connection (host/port/user/password/database/secure + DSN-override).
- ForecastDefaults / TimesFMSettings / CalibrationSettings — nested forecast settings blocks.
- ForecastSettings — forecast section (defaults, quantiles, TimesFM parameters, calibration).
- AgentSettings — agent section (model, lags, dependency-analysis methods).
- McpSettings — MCP-server section (host/port).
- SchedulerSettings — built-in scheduler section (host/port + retry/misfire policy).
- Settings — aggregate of all sections.
- get_settings(refresh=False) -> Settings — cached settings (reads NORN_CONFIG_DIR; refresh resets the cache).
"""
from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import ClassVar

import yaml
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
    # Secret keys that must NEVER come from the YAML file (env-only). A YAML file
    # containing one of these fails loudly instead of silently accepting a secret
    # that would likely end up committed to version control.
    YAML_FORBIDDEN: ClassVar[tuple[str, ...]] = ()

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
        # --- YAML source: the section file from the NORN_CONFIG_DIR directory ---
        # Explicit, diagnosable failure if the config file is not found (e.g. in a
        # container/k8s, where cwd != repo root): otherwise pydantic silently ignores
        # the missing YAML and fails with a generic "field required".
        yaml_path = _config_dir() / cls.YAML_FILE
        if not yaml_path.is_file():
            raise FileNotFoundError(
                f"norn config file not found: {yaml_path}. Set NORN_CONFIG_DIR to the "
                f"directory containing {cls.YAML_FILE} (and the other section YAMLs). "
                f"cwd={Path.cwd()}."
            )
        # --- secrets are env-only: refuse a YAML file that tries to supply one ---
        # (read the raw file: the settings source resolves its payload lazily)
        if cls.YAML_FORBIDDEN:
            payload = yaml.safe_load(yaml_path.read_text()) or {}
            leaked = [k for k in cls.YAML_FORBIDDEN if payload.get(k) is not None]
            if leaked:
                raise ValueError(
                    f"{yaml_path}: {', '.join(leaked)} must not be set in YAML — these are "
                    f"secrets and come only from environment variables (see config docs)."
                )
        yaml_src = YamlConfigSettingsSource(settings_cls, yaml_file=yaml_path)
        # --- order = priority: init kwargs (tests) > env > yaml (no field defaults) ---
        return (init_settings, env_settings, yaml_src)


class DatabaseSettings(_YamlSection):
    model_config = SettingsConfigDict(env_prefix="NORN_DB_", env_nested_delimiter="__", extra="ignore")
    YAML_FILE: ClassVar[str] = "database.yml"
    YAML_FORBIDDEN: ClassVar[tuple[str, ...]] = ("password", "dsn")
    host: str
    port: int
    user: str
    database: str
    secure: bool
    manage_schema: bool   # true: norn creates contract tables; false: INSERT-only, DDL is external
    # secret: ONLY from env NORN_DB_PASSWORD (not in YAML, no default)
    password: str = Field(validation_alias=AliasChoices("NORN_DB_PASSWORD", "password"))
    # the only optional override (env NORN_CLICKHOUSE_URL): None = override not set
    dsn: str | None = Field(default=None, validation_alias=AliasChoices("NORN_CLICKHOUSE_URL", "dsn"))


class ForecastDefaults(BaseModel):
    # same lower bounds as ForecastJob: resolved() merges these via model_copy,
    # which does NOT re-run ForecastJob's field validators
    horizon: int = Field(ge=1)
    context_length: int = Field(ge=1)
    seasonality: int = Field(ge=1)  # 0 would divide by zero in the baseline


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
    base_url: str | None        # ollama: URL required; cloud: null. No implicit fallback.
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
    # --- aggregate assembly: each section reads its own YAML + env on instantiation ---
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
