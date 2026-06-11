"""
packages/core/src/norn_core/clickhouse.py

The platform's single connection point to the warehouse (ClickHouse). Encapsulates parsing
the connection string (DSN) and assembling the client configuration from the config layer, so that all
norn services (forecast-worker, agent, integration layer) open the connection
the same way and do not duplicate the parsing and port/protocol selection logic.

Methods:
- parse_dsn(dsn) -> dict — parse a DSN into connection parameters (host/port/user/password/database/secure),
  substituting default ports (8443 for https, 8123 for http) and checking that the DB name is present.
- get_client(dsn=None) -> Client — assemble a ClickHouse client: from an explicit DSN, or from the config layer,
  where the DSN from env NORN_CLICKHOUSE_URL takes priority over the per-file host/port/...
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

import clickhouse_connect
from clickhouse_connect.driver.client import Client

# clickhouse-connect binds VALUES via parameters, but it cannot bind SQL
# identifiers (table/column names) — those must be interpolated. Restrict any
# interpolated identifier to a safe shape so attacker-controlled job fields
# cannot inject SQL (defense-in-depth). Allows a single dotted db.table form;
# rejects `db..table` / trailing dots, which would otherwise pass the "safe"
# check and surface as a confusing ClickHouse syntax error.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")


def _safe_identifier(name: str) -> str:
    """Return `name` if it is a safe SQL identifier, else raise ValueError."""
    if not isinstance(name, str) or not _IDENTIFIER_RE.match(name):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")
    return name


def parse_dsn(dsn: str) -> dict:
    # --- DSN parsing: the scheme sets connection security, the path is the DB name ---
    u = urlparse(dsn)
    secure = u.scheme == "https"
    database = u.path.lstrip("/")
    if not database:
        # Never echo the DSN — it carries the ClickHouse password (credential leak).
        raise ValueError("ClickHouse DSN is missing the database path component")
    # --- parameter assembly: the default port depends on the protocol ---
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
    # --- selecting the connection configuration source ---
    if dsn is not None:
        # an explicit DSN from the caller has the highest priority
        cfg = parse_dsn(dsn)
    else:
        db = _db_settings()
        if db.dsn:
            # the DSN from the config layer (env NORN_CLICKHOUSE_URL) overrides the per-file fields
            cfg = parse_dsn(db.dsn)
        else:
            # per-file host/port/user/... as a fallback
            cfg = {
                "host": db.host, "port": db.port, "username": db.user,
                "password": db.password, "database": db.database, "secure": db.secure,
            }
    return clickhouse_connect.get_client(**cfg)
