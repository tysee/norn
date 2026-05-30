"""
packages/core/src/norn_core/clickhouse.py

Единая точка подключения платформы к warehouse (ClickHouse). Инкапсулирует разбор
строки подключения (DSN) и сборку конфигурации клиента из config-слоя, чтобы все
сервисы norn (forecast-воркер, агент, integration-слой) открывали соединение
одинаково и не дублировали логику парсинга и выбора порта/протокола.

Методы:
- parse_dsn(dsn) -> dict — разбор DSN в параметры подключения (host/port/user/password/database/secure),
  с подстановкой портов по умолчанию (8443 для https, 8123 для http) и проверкой наличия имени БД.
- get_client(dsn=None) -> Client — собирает клиент ClickHouse: из явного DSN, либо из config-слоя,
  где DSN из env NORN_CLICKHOUSE_URL имеет приоритет над пофайловыми host/port/...
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

import clickhouse_connect
from clickhouse_connect.driver.client import Client

# clickhouse-connect binds VALUES via parameters, but it cannot bind SQL
# identifiers (table/column names) — those must be interpolated. Restrict any
# interpolated identifier to a safe shape so attacker-controlled job fields
# cannot inject SQL (defense-in-depth). Allows dotted db.table forms.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


def _safe_identifier(name: str) -> str:
    """Return `name` if it is a safe SQL identifier, else raise ValueError."""
    if not isinstance(name, str) or not _IDENTIFIER_RE.match(name):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")
    return name


def parse_dsn(dsn: str) -> dict:
    # --- разбор DSN: схема задаёт защищённость соединения, путь — имя БД ---
    u = urlparse(dsn)
    secure = u.scheme == "https"
    database = u.path.lstrip("/")
    if not database:
        # Never echo the DSN — it carries the ClickHouse password (credential leak).
        raise ValueError("ClickHouse DSN is missing the database path component")
    # --- сборка параметров: порт по умолчанию зависит от протокола ---
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

    return get_settings().database


def get_client(dsn: str | None = None) -> Client:
    # --- выбор источника конфигурации подключения ---
    if dsn is not None:
        # явный DSN от вызывающего имеет наивысший приоритет
        cfg = parse_dsn(dsn)
    else:
        db = _db_settings()
        if db.dsn:
            # DSN из config-слоя (env NORN_CLICKHOUSE_URL) переопределяет пофайловые поля
            cfg = parse_dsn(db.dsn)
        else:
            # пофайловые host/port/user/... как fallback
            cfg = {
                "host": db.host, "port": db.port, "username": db.user,
                "password": db.password, "database": db.database, "secure": db.secure,
            }
    return clickhouse_connect.get_client(**cfg)
