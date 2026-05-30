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

from importlib.resources import files

from clickhouse_connect.driver.client import Client


def schema_sql() -> str:
    return files("norn_integration").joinpath("schema.sql").read_text()


def apply_schema(client: Client) -> None:
    # --- split: режем контракт по ';' на отдельные DDL-операторы ---
    for stmt in (s.strip() for s in schema_sql().split(";")):
        # --- apply: пропускаем пустые хвосты, накатываем каждый оператор ---
        if stmt:
            client.command(stmt)
