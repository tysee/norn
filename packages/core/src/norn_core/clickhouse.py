"""
packages/core/src/norn_core/clickhouse.py

Фабрика клиента ClickHouse и парсер DSN. Единая точка подключения к warehouse.

Методы:
- parse_dsn(dsn) -> dict — разбор строки подключения (host/port/user/db/secure).
- get_client(dsn=None) -> Client — клиент из DSN или из config-слоя (env NORN_CLICKHOUSE_URL переопределяет).
"""
from __future__ import annotations

from urllib.parse import urlparse

import clickhouse_connect
from clickhouse_connect.driver.client import Client


def parse_dsn(dsn: str) -> dict:
    u = urlparse(dsn)
    secure = u.scheme == "https"
    database = u.path.lstrip("/")
    if not database:
        # Never echo the DSN — it carries the ClickHouse password (credential leak).
        raise ValueError("ClickHouse DSN is missing the database path component")
    return {
        "host": u.hostname,
        "port": u.port or (8443 if secure else 8123),
        "username": u.username or "default",
        "password": u.password or "",
        "database": database,
        "secure": secure,
    }


def _db_settings():
    from norn_core.config import get_settings

    return get_settings(refresh=True).database


def get_client(dsn: str | None = None) -> Client:
    if dsn is not None:
        cfg = parse_dsn(dsn)
    else:
        db = _db_settings()
        if db.dsn:
            cfg = parse_dsn(db.dsn)
        else:
            cfg = {
                "host": db.host, "port": db.port, "username": db.user,
                "password": db.password, "database": db.database, "secure": db.secure,
            }
    return clickhouse_connect.get_client(**cfg)
