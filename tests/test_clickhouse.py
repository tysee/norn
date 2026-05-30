import pytest

from norn_core.clickhouse import parse_dsn


def test_parse_dsn_full():
    cfg = parse_dsn("http://norn:secret@db.example.com:8123/analytics")
    assert cfg == {
        "host": "db.example.com",
        "port": 8123,
        "username": "norn",
        "password": "secret",
        "database": "analytics",
        "secure": False,
    }


def test_parse_dsn_https_default_port():
    cfg = parse_dsn("https://user:pw@host/db")
    assert cfg["port"] == 8443
    assert cfg["secure"] is True


def test_parse_dsn_requires_database():
    with pytest.raises(ValueError):
        parse_dsn("http://user:pw@host:8123/")


def test_parse_dsn_error_does_not_leak_credentials():
    # The error must never echo the DSN — it carries the password.
    with pytest.raises(ValueError) as exc:
        parse_dsn("http://user:supersecret@host:8123/")
    assert "supersecret" not in str(exc.value)


def test_get_client_builds_from_settings(monkeypatch, tmp_path):
    # No DSN env -> client config comes from settings.database fields.
    import norn_core.clickhouse as ch
    from norn_core.config import DatabaseSettings

    monkeypatch.delenv("NORN_CLICKHOUSE_URL", raising=False)
    captured = {}
    monkeypatch.setattr(ch.clickhouse_connect, "get_client", lambda **kw: captured.update(kw) or "CLIENT")
    monkeypatch.setattr(ch, "_db_settings", lambda: DatabaseSettings(
        host="h", port=8123, user="u", password="p", database="d", secure=False, dsn=None
    ))
    out = ch.get_client()
    assert out == "CLIENT"
    assert captured["host"] == "h" and captured["database"] == "d" and captured["username"] == "u"
