import os

import pytest

from norn_core.clickhouse import get_client
from norn_integration.schema import apply_schema

DSN = os.environ.get("NORN_CLICKHOUSE_URL", "http://norn:norn@localhost:8123/norn")


@pytest.fixture(scope="session")
def ch():
    client = get_client(DSN)
    apply_schema(client)
    yield client
    client.close()


@pytest.fixture(autouse=True)
def _reset(ch):
    ch.command("TRUNCATE TABLE IF EXISTS forecast_point")
    ch.command("TRUNCATE TABLE IF EXISTS forecast_run")
    ch.command("DROP TABLE IF EXISTS test_mart")
    yield
