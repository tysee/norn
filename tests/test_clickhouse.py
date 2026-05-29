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
