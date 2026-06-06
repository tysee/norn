"""
packages/integration/src/norn_integration/schema.py

Idempotent application of the table DDL contract to the ClickHouse analytical
store. The module loads the declarative SQL contract (schema.sql, shipped with
the package) and applies it to the cluster: every statement is written as
CREATE TABLE IF NOT EXISTS, so re-running is safe and serves as the
initialization/migration point for the entire norn platform's store.

Public functions:
- schema_sql() -> str — returns the DDL contract text, read from the schema.sql
  resource inside the package (source of truth for the table structure).
- apply_schema(client) -> None — splits the contract into individual statements
  and executes each one against the given ClickHouse client, creating the
  missing tables.
"""
from __future__ import annotations

import re
from importlib.resources import files

from clickhouse_connect.driver.client import Client


def schema_sql(retention_months: int = 12) -> str:
    """DDL contract text with the TTL token `{RETENTION_MONTHS_TTL}` substituted.

    retention_months > 0 -> token is replaced with `TTL created_at + INTERVAL N MONTH`.
    retention_months == 0 -> token is stripped (partitioning without auto-deletion).
    Table names/structure do not depend on retention (important for required_tables()).
    """
    raw = files("norn_integration").joinpath("schema.sql").read_text()
    if retention_months and int(retention_months) > 0:
        return raw.replace(
            "{RETENTION_MONTHS_TTL}",
            f"TTL created_at + INTERVAL {int(retention_months)} MONTH",
        )
    return raw.replace("{RETENTION_MONTHS_TTL}", "")  # 0 -> no TTL


def apply_schema(client: Client, retention_months: int = 12) -> None:
    # --- split: cut the contract on ';' into individual DDL statements ---
    for stmt in (s.strip() for s in schema_sql(retention_months).split(";")):
        # --- apply: skip empty trailing fragments, run each statement ---
        if stmt:
            client.command(stmt)


_TABLE_RE = re.compile(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", re.IGNORECASE)


def required_tables() -> list[str]:
    """Contract table names — single source = schema.sql (no second copy)."""
    return _TABLE_RE.findall(schema_sql(0))  # table names independent of TTL


class ContractSchemaMissing(RuntimeError):
    """Contract tables are missing while manage_schema=false (DDL is the user's responsibility)."""


def prepare_schema(
    client: Client, manage_schema: bool, retention_months: int = 12
) -> None:
    """Prepare the schema before writing.

    manage_schema=true  -> apply_schema (CREATE IF NOT EXISTS, as it is now).
    manage_schema=false -> check that contract tables exist; if missing -> ContractSchemaMissing.
    """
    if manage_schema:
        apply_schema(client, retention_months)
        return
    missing = [
        t for t in required_tables()
        if str(client.command(f"EXISTS TABLE {t}")).strip() not in ("1", "True")
    ]
    if missing:
        raise ContractSchemaMissing(
            "contract tables not found and database.manage_schema=false: "
            f"{', '.join(missing)}. Create them with your dbt/migrations "
            "(`norn print-schema` prints the canonical DDL) or set manage_schema=true."
        )
