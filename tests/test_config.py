import textwrap

from norn_core.config import DatabaseSettings, get_settings


def _write_config(d):
    (d / "database.yml").write_text("host: chhost\nport: 8123\nuser: norn\ndatabase: norn\nsecure: false\n")
    (d / "forecast.yml").write_text(textwrap.dedent("""\
        defaults: {horizon: 30, context_length: 512, seasonality: 7}
        quantiles: [0.1, 0.5, 0.9]
        timesfm: {worker_url: "http://localhost:9100", max_context: 1024, max_horizon: 1024}
        calibration: {n_cutoffs: 3}
    """))
    (d / "agent.yml").write_text("model: m1\nmax_lag: 10\ncontext_length: 512\nmethods: [a, b]\ngranger_min_points_factor: 3\n")
    (d / "mcp.yml").write_text("host: 127.0.0.1\nport: 9200\n")


def test_settings_load_from_yaml(tmp_path, monkeypatch):
    _write_config(tmp_path)
    monkeypatch.setenv("NORN_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("NORN_CLICKHOUSE_URL", raising=False)
    s = get_settings(refresh=True)
    assert s.database.host == "chhost"
    assert s.forecast.defaults.horizon == 30
    assert s.forecast.timesfm.worker_url == "http://localhost:9100"
    assert s.agent.model == "m1"
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
    (tmp_path / "agent.yml").write_text(
        "model: m\nmax_lag: 10\ncontext_length: 512\n"
        "methods: [a]\ngranger_min_points_factor: 3\ngranger_significance: 0.05\n"
    )
    for n in ("database", "forecast", "mcp"):
        (tmp_path / f"{n}.yml").write_text("{}\n")
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

    assert "YAML_FILE" not in DatabaseSettings().model_dump()

    # Real config: database.yml has host=chhost; the "decoy" file does not.
    (tmp_path / "database.yml").write_text("host: chhost\n")
    (tmp_path / "decoy.yml").write_text("host: decoyhost\n")
    monkeypatch.setenv("NORN_DB_YAML_FILE", "decoy.yml")

    # Loader still uses the class default (database.yml), ignoring the env override.
    assert DatabaseSettings().host == "chhost"


def test_forecast_covariates_settings(tmp_path, monkeypatch):
    (tmp_path / "forecast.yml").write_text(
        "defaults: {horizon: 30, context_length: 512, seasonality: 7}\n"
        "quantiles: [0.1, 0.5, 0.9]\n"
        "timesfm: {worker_url: u, max_context: 1024, max_horizon: 1024}\n"
        "calibration: {n_cutoffs: 3}\n"
        "covariates: {horizon_policy: strict, xreg_mode: 'xreg+timesfm'}\n"
    )
    for n in ("database", "agent", "mcp"):
        (tmp_path / f"{n}.yml").write_text("{}\n")
    monkeypatch.setenv("NORN_CONFIG_DIR", str(tmp_path))
    from norn_core.config import get_settings
    s = get_settings(refresh=True)
    assert s.forecast.covariates.horizon_policy == "strict"
    assert s.forecast.covariates.xreg_mode == "xreg+timesfm"


def test_agent_provider_defaults_and_override(tmp_path, monkeypatch):
    (tmp_path / "agent.yml").write_text(
        "max_lag: 10\ncontext_length: 512\nmethods: [a]\n"
        "granger_min_points_factor: 3\ngranger_significance: 0.05\n"
        "provider: ollama\nmodel: gemma3n:e2b\nbase_url: null\n"
    )
    for n in ("database", "forecast", "mcp"):
        (tmp_path / f"{n}.yml").write_text("{}\n")
    monkeypatch.setenv("NORN_CONFIG_DIR", str(tmp_path))
    from norn_core.config import get_settings
    s = get_settings(refresh=True)
    assert s.agent.provider == "ollama"
    assert s.agent.model == "gemma3n:e2b"
    assert s.agent.base_url is None
    monkeypatch.setenv("NORN_AGENT_PROVIDER", "anthropic-api")
    assert get_settings(refresh=True).agent.provider == "anthropic-api"  # env overrides yaml


def test_get_settings_is_cached_within_a_run(tmp_path, monkeypatch):
    # Two get_settings() calls (no refresh) must return the SAME object,
    # proving the lru_cache holds on the hot forecast path.
    _write_config(tmp_path)
    monkeypatch.setenv("NORN_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("NORN_CLICKHOUSE_URL", raising=False)
    first = get_settings()
    second = get_settings()
    assert first is second
