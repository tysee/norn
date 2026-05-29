from __future__ import annotations

from importlib.resources import files

from clickhouse_connect.driver.client import Client


def schema_sql() -> str:
    return files("norn_integration").joinpath("schema.sql").read_text()


def apply_schema(client: Client) -> None:
    for stmt in (s.strip() for s in schema_sql().split(";")):
        if stmt:
            client.command(stmt)
