from __future__ import annotations

import os
from urllib.parse import urlparse

import clickhouse_connect
from clickhouse_connect.driver.client import Client

DEFAULT_DSN = "http://norn:norn@localhost:8123/norn"


def parse_dsn(dsn: str) -> dict:
    u = urlparse(dsn)
    secure = u.scheme == "https"
    database = u.path.lstrip("/")
    if not database:
        raise ValueError(f"DSN missing database: {dsn!r}")
    return {
        "host": u.hostname,
        "port": u.port or (8443 if secure else 8123),
        "username": u.username or "default",
        "password": u.password or "",
        "database": database,
        "secure": secure,
    }


def get_client(dsn: str | None = None) -> Client:
    cfg = parse_dsn(dsn or os.environ.get("NORN_CLICKHOUSE_URL", DEFAULT_DSN))
    return clickhouse_connect.get_client(**cfg)
