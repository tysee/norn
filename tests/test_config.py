import textwrap

import pytest
from pydantic import ValidationError

from norn_core.config import DatabaseSettings, get_settings


def _write_config(d):
    (d / "database.yml").write_text(
        "host: chhost\nport: 8123\nuser: norn\ndatabase: norn\nsecure: false\nmanage_schema: true\n")  # password via env
    (d / "forecast.yml").write_text(textwrap.dedent("""\
        defaults: {horizon: 30, context_length: 512, seasonality: 7}
        quantiles: [0.1, 0.5, 0.9]
        timesfm: {worker_url: "http://localhost:9100", max_context: 1024, max_horizon: 1024}
        calibration: {n_cutoffs: 3}
        covariates: {horizon_policy: strict, xreg_mode: "xreg+timesfm"}
        retention_months: 12
    """))
    (d / "agent.yml").write_text(textwrap.dedent("""\
        provider: ollama
        model: gemma4:e2b
        base_url: http://localhost:11434/v1
        output_mode: native
        max_lag: 10
        context_length: 512
        methods: [lagged_cross_correlation, granger]
        granger_min_points_factor: 3
        granger_significance: 0.05
        worker_url: null
    """))
    (d / "mcp.yml").write_text("host: 127.0.0.1\nport: 9200\n")
    (d / "scheduler.yml").write_text(
        "host: 127.0.0.1\nport: 9300\nretries: 2\nretry_base_seconds: 30\nmisfire_grace_seconds: 3600\n")


def test_settings_load_from_yaml(tmp_path, monkeypatch):
    _write_config(tmp_path)
    monkeypatch.setenv("NORN_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("NORN_CLICKHOUSE_URL", raising=False)
    s = get_settings(refresh=True)
    assert s.database.host == "chhost"
    assert s.forecast.defaults.horizon == 30
    assert s.forecast.timesfm.worker_url == "http://localhost:9100"
    assert s.agent.model == "gemma4:e2b"
    assert s.agent.output_mode == "native"
    assert s.mcp.port == 9200


def test_env_overrides_yaml(tmp_path, monkeypatch):
    _write_config(tmp_path)
    monkeypatch.setenv("NORN_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("NORN_MCP_PORT", "9999")
    monkeypatch.setenv("NORN_AGENT_MODEL", "override-model")
    s = get_settings(refresh=True)
    assert s.mcp.port == 9999          # env wins over yaml
    assert s.agent.model == "override-model"


def test_clickhouse_url_alias_overrides_db(tmp_path, monkeypatch):
    _write_config(tmp_path)
    monkeypatch.setenv("NORN_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("NORN_CLICKHOUSE_URL", "http://u:p@h:8123/db")
    s = get_settings(refresh=True)
    assert s.database.dsn == "http://u:p@h:8123/db"


def test_agent_granger_settings(tmp_path, monkeypatch):
    _write_config(tmp_path)
    (tmp_path / "agent.yml").write_text(textwrap.dedent("""\
        provider: ollama
        model: m
        base_url: http://localhost:11434/v1
        output_mode: native
        max_lag: 10
        context_length: 512
        methods: [a]
        granger_min_points_factor: 3
        granger_significance: 0.05
    """))
    monkeypatch.setenv("NORN_CONFIG_DIR", str(tmp_path))
    from norn_core.config import get_settings
    s = get_settings(refresh=True)
    assert s.agent.granger_significance == 0.05
    assert s.agent.granger_min_points_factor == 3


def test_yaml_file_not_a_setting_field(tmp_path, monkeypatch):
    # YAML_FILE is a ClassVar, not an overridable settings field:
    # (1) it must not appear in model_dump(), and
    # (2) setting NORN_DB_YAML_FILE must NOT change which file is loaded.
    monkeypatch.setenv("NORN_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("NORN_CLICKHOUSE_URL", raising=False)

    # Real config: database.yml has host=chhost; the "decoy" file does not.
    (tmp_path / "database.yml").write_text(
        "host: chhost\nport: 8123\nuser: norn\ndatabase: norn\nsecure: false\nmanage_schema: true\n")
    (tmp_path / "decoy.yml").write_text(
        "host: decoyhost\nport: 8123\nuser: norn\ndatabase: norn\nsecure: false\nmanage_schema: true\n")

    assert "YAML_FILE" not in DatabaseSettings().model_dump()

    monkeypatch.setenv("NORN_DB_YAML_FILE", "decoy.yml")

    # Loader still uses the class default (database.yml), ignoring the env override.
    assert DatabaseSettings().host == "chhost"


def test_forecast_covariates_settings(tmp_path, monkeypatch):
    _write_config(tmp_path)
    (tmp_path / "forecast.yml").write_text(
        "defaults: {horizon: 30, context_length: 512, seasonality: 7}\n"
        "quantiles: [0.1, 0.5, 0.9]\n"
        "timesfm: {worker_url: u, max_context: 1024, max_horizon: 1024}\n"
        "calibration: {n_cutoffs: 3}\n"
        "covariates: {horizon_policy: strict, xreg_mode: 'xreg+timesfm'}\n"
        "retention_months: 12\n"
    )
    monkeypatch.setenv("NORN_CONFIG_DIR", str(tmp_path))
    from norn_core.config import get_settings
    s = get_settings(refresh=True)
    assert s.forecast.covariates.horizon_policy == "strict"
    assert s.forecast.covariates.xreg_mode == "xreg+timesfm"


def test_missing_required_agent_key_raises(tmp_path, monkeypatch):
    _write_config(tmp_path)
    # drop a required key -> must fail loudly, not fall back to a default
    (tmp_path / "agent.yml").write_text(
        "provider: ollama\nbase_url: null\noutput_mode: native\nmax_lag: 10\n"
        "context_length: 512\nmethods: [granger]\ngranger_min_points_factor: 3\n"
        "granger_significance: 0.05\n")  # 'model' omitted
    monkeypatch.setenv("NORN_CONFIG_DIR", str(tmp_path))
    from norn_core.config import get_settings
    with pytest.raises(ValidationError):
        get_settings(refresh=True)


def test_missing_db_password_raises(tmp_path, monkeypatch):
    _write_config(tmp_path)
    monkeypatch.setenv("NORN_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("NORN_DB_PASSWORD", raising=False)
    monkeypatch.delenv("NORN_CLICKHOUSE_URL", raising=False)
    from norn_core.config import DatabaseSettings
    with pytest.raises(ValidationError):
        DatabaseSettings()


def test_env_password_loads(tmp_path, monkeypatch):
    _write_config(tmp_path)
    monkeypatch.setenv("NORN_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("NORN_DB_PASSWORD", "sekret")
    monkeypatch.delenv("NORN_CLICKHOUSE_URL", raising=False)
    from norn_core.config import DatabaseSettings
    assert DatabaseSettings().password == "sekret"


def test_get_settings_is_cached_within_a_run(tmp_path, monkeypatch):
    # Two get_settings() calls (no refresh) must return the SAME object,
    # proving the lru_cache holds on the hot forecast path.
    _write_config(tmp_path)
    monkeypatch.setenv("NORN_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("NORN_CLICKHOUSE_URL", raising=False)
    first = get_settings()
    second = get_settings()
    assert first is second


def test_missing_config_dir_raises_clear_error(tmp_path, monkeypatch):
    # NORN_CONFIG_DIR pointing at a dir without the section YAMLs must fail LOUDLY
    # (clear FileNotFoundError), not via an obscure "field required" ValidationError.
    monkeypatch.setenv("NORN_CONFIG_DIR", str(tmp_path / "does-not-exist"))
    from norn_core.config import get_settings
    with pytest.raises(FileNotFoundError):
        get_settings(refresh=True)


def test_forecast_retention_months(tmp_path, monkeypatch):
    _write_config(tmp_path)
    monkeypatch.setenv("NORN_CONFIG_DIR", str(tmp_path))
    from norn_core.config import get_settings
    assert get_settings(refresh=True).forecast.retention_months == 12


def test_manage_schema_loads_and_overrides(tmp_path, monkeypatch):
    _write_config(tmp_path)
    monkeypatch.setenv("NORN_CONFIG_DIR", str(tmp_path))
    from norn_core.config import get_settings
    assert get_settings(refresh=True).database.manage_schema is True
    monkeypatch.setenv("NORN_DB_MANAGE_SCHEMA", "false")
    assert get_settings(refresh=True).database.manage_schema is False


def test_scheduler_section_loads(tmp_path, monkeypatch):
    _write_config(tmp_path)
    monkeypatch.setenv("NORN_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("NORN_DB_PASSWORD", "x")
    s = get_settings(refresh=True)
    assert s.scheduler.port == 9300
    assert s.scheduler.retries == 2
    assert s.agent.worker_url is None


def test_scheduler_env_overrides(tmp_path, monkeypatch):
    _write_config(tmp_path)
    monkeypatch.setenv("NORN_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("NORN_DB_PASSWORD", "x")
    monkeypatch.setenv("NORN_SCHEDULER_PORT", "9999")
    monkeypatch.setenv("NORN_AGENT_WORKER_URL", "http://agent:9400")
    s = get_settings(refresh=True)
    assert s.scheduler.port == 9999
    assert s.agent.worker_url == "http://agent:9400"


def test_password_in_yaml_is_rejected(tmp_path, monkeypatch):
    # the secret is env-only by contract; a YAML `password:` would otherwise be
    # silently accepted and likely committed (review finding F-2)
    _write_config(tmp_path)
    (tmp_path / "database.yml").write_text(
        "host: chhost\nport: 8123\nuser: norn\ndatabase: norn\nsecure: false\n"
        "manage_schema: true\npassword: from_yaml\n")
    monkeypatch.setenv("NORN_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("NORN_CLICKHOUSE_URL", raising=False)
    with pytest.raises(ValueError, match="password"):
        get_settings(refresh=True)


def test_env_password_alias_in_yaml_is_rejected(tmp_path, monkeypatch):
    # Env-style aliases are still YAML keys if they appear in the file; they must
    # be rejected too, otherwise the env-only secret contract is bypassed.
    _write_config(tmp_path)
    (tmp_path / "database.yml").write_text(
        "host: chhost\nport: 8123\nuser: norn\ndatabase: norn\nsecure: false\n"
        "manage_schema: true\nNORN_DB_PASSWORD: from_yaml\n")
    monkeypatch.setenv("NORN_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("NORN_DB_PASSWORD", raising=False)
    monkeypatch.delenv("NORN_CLICKHOUSE_URL", raising=False)
    with pytest.raises(ValueError, match="NORN_DB_PASSWORD"):
        DatabaseSettings()


def test_dsn_in_yaml_is_rejected(tmp_path, monkeypatch):
    _write_config(tmp_path)
    (tmp_path / "database.yml").write_text(
        "host: chhost\nport: 8123\nuser: norn\ndatabase: norn\nsecure: false\n"
        "manage_schema: true\ndsn: http://u:p@h:8123/db\n")
    monkeypatch.setenv("NORN_CONFIG_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="dsn"):
        get_settings(refresh=True)


def test_env_dsn_alias_in_yaml_is_rejected(tmp_path, monkeypatch):
    _write_config(tmp_path)
    (tmp_path / "database.yml").write_text(
        "host: chhost\nport: 8123\nuser: norn\ndatabase: norn\nsecure: false\n"
        "manage_schema: true\nNORN_CLICKHOUSE_URL: http://u:p@h:8123/db\n")
    monkeypatch.setenv("NORN_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("NORN_DB_PASSWORD", "env-secret")
    monkeypatch.delenv("NORN_CLICKHOUSE_URL", raising=False)
    with pytest.raises(ValueError, match="NORN_CLICKHOUSE_URL"):
        DatabaseSettings()
