"""
packages/integration/src/norn_integration/schema.py

Идемпотентное применение DDL-контракта таблиц к аналитическому хранилищу
ClickHouse. Модуль загружает декларативный SQL-контракт (schema.sql,
поставляется вместе с пакетом) и накатывает его на кластер: все операторы
оформлены как CREATE TABLE IF NOT EXISTS, поэтому повторный прогон безопасен и
служит точкой инициализации/миграции хранилища для всей платформы norn.

Публичные функции:
- schema_sql() -> str — возвращает текст DDL-контракта, прочитанный из ресурса
  schema.sql внутри пакета (источник истины по структуре таблиц).
- apply_schema(client) -> None — разбивает контракт на отдельные операторы и
  выполняет каждый на переданном ClickHouse-клиенте, создавая отсутствующие
  таблицы.
"""
from __future__ import annotations

import re
from importlib.resources import files

from clickhouse_connect.driver.client import Client


def schema_sql(retention_months: int = 12) -> str:
    """Текст DDL-контракта с подставленным TTL-токеном `{RETENTION_MONTHS_TTL}`.

    retention_months > 0 -> токен заменяется на `TTL created_at + INTERVAL N MONTH`.
    retention_months == 0 -> токен вырезается (партиционирование без авто-удаления).
    Имена/структура таблиц от retention не зависят (важно для required_tables()).
    """
    raw = files("norn_integration").joinpath("schema.sql").read_text()
    if retention_months and int(retention_months) > 0:
        return raw.replace(
            "{RETENTION_MONTHS_TTL}",
            f"TTL created_at + INTERVAL {int(retention_months)} MONTH",
        )
    return raw.replace("{RETENTION_MONTHS_TTL}", "")  # 0 -> no TTL


def apply_schema(client: Client, retention_months: int = 12) -> None:
    # --- split: режем контракт по ';' на отдельные DDL-операторы ---
    for stmt in (s.strip() for s in schema_sql(retention_months).split(";")):
        # --- apply: пропускаем пустые хвосты, накатываем каждый оператор ---
        if stmt:
            client.command(stmt)


_TABLE_RE = re.compile(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", re.IGNORECASE)


def required_tables() -> list[str]:
    """Имена контракт-таблиц — единственный источник = schema.sql (без второй копии)."""
    return _TABLE_RE.findall(schema_sql(0))  # table names independent of TTL


class ContractSchemaMissing(RuntimeError):
    """Контракт-таблицы отсутствуют, а manage_schema=false (DDL — ответственность пользователя)."""


def prepare_schema(
    client: Client, manage_schema: bool, retention_months: int = 12
) -> None:
    """Подготовка схемы перед записью.

    manage_schema=true  -> apply_schema (CREATE IF NOT EXISTS, как сейчас).
    manage_schema=false -> проверить наличие контракт-таблиц; при нехватке -> ContractSchemaMissing.
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
