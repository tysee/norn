from __future__ import annotations

"""
packages/integration/src/norn_integration/schema.py

Применение DDL контракта прогнозов к ClickHouse (идемпотентно).

Методы:
- schema_sql() -> str — читает schema.sql из пакета.
- apply_schema(client) -> None — выполняет все CREATE TABLE IF NOT EXISTS.
"""

from importlib.resources import files

from clickhouse_connect.driver.client import Client


def schema_sql() -> str:
    return files("norn_integration").joinpath("schema.sql").read_text()


def apply_schema(client: Client) -> None:
    for stmt in (s.strip() for s in schema_sql().split(";")):
        if stmt:
            client.command(stmt)
