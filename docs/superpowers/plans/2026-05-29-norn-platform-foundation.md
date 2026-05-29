# norn Platform Foundation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the generic norn forecasting spine — a uv monorepo whose `norn up` brings ClickHouse up in Docker, applies the forecast-contract schema, and runs a baseline forecaster that reads a metric series from ClickHouse and writes calibrated forecast points back, end-to-end and tested.

**Architecture:** A uv workspace with four packages (`core`, `integration`, `forecast`, plus a `cli`). `core` holds the shared contract (pydantic models for the forecast-job YAML and the `forecast_point` row) and the ClickHouse client factory. `integration` owns idempotent ClickHouse DDL for the forecast tables norn writes. `forecast` holds a pure baseline forecaster (seasonal-naive with empirical intervals) and a runner that does extract → forecast → write-back. `cli` (typer) wires `norn up / schema-apply / forecast`. TimesFM is intentionally NOT here — it is Plan 2; the baseline keeps the pipeline runnable and the worker interface stable.

**Tech Stack:** Python ≥3.12 (target 3.14 once torch/dbt wheels confirmed — `monorepo-and-data-model.md` §5), uv workspace, pydantic v2, PyYAML, `clickhouse-connect`, numpy, typer, pytest. ClickHouse via Docker Compose.

---

## File Structure

```text
norn/
├── pyproject.toml                              # uv workspace root + dev deps (pytest)
├── packages/
│   ├── core/
│   │   ├── pyproject.toml
│   │   └── src/norn_core/
│   │       ├── __init__.py
│   │       ├── contract.py                     # Grain, ForecastJob, ForecastPoint
│   │       └── clickhouse.py                   # get_client(dsn) URL parser
│   ├── integration/
│   │   ├── pyproject.toml
│   │   └── src/norn_integration/
│   │       ├── __init__.py
│   │       ├── schema.sql                       # forecast_run + forecast_point DDL
│   │       └── schema.py                         # apply_schema(client)
│   └── forecast/
│       ├── pyproject.toml
│       └── src/norn_forecast/
│           ├── __init__.py
│           ├── baseline.py                       # seasonal_naive_forecast() — pure
│           └── runner.py                          # run_job() extract→forecast→write
├── cli/
│   ├── pyproject.toml
│   └── src/norn_cli/
│       ├── __init__.py
│       └── main.py                                # typer: up / schema-apply / forecast
├── deploy/
│   └── docker-compose.yml                         # clickhouse service
├── forecasts/
│   └── example.yml                                # generic example forecast-job (not crypto)
├── tests/
│   ├── conftest.py                                # ClickHouse session fixture + table reset
│   ├── test_contract.py
│   ├── test_clickhouse.py
│   ├── test_schema.py
│   ├── test_baseline.py
│   ├── test_runner.py
│   └── test_cli.py
└── docs/integration/lightdash.md                  # how to point Lightdash at norn (instructions only)
```

**Test database:** tests run against a local ClickHouse container (`database.md`: spin up in Docker for tests, never an on-device DB). Connection from `NORN_CLICKHOUSE_URL`, default `http://norn:norn@localhost:8123/norn`. Each test session creates a fresh schema and truncates tables.

---

### Task 1: uv workspace + package skeletons

**Files:**
- Create: `pyproject.toml`
- Create: `packages/core/pyproject.toml`, `packages/core/src/norn_core/__init__.py`
- Create: `packages/integration/pyproject.toml`, `packages/integration/src/norn_integration/__init__.py`
- Create: `packages/forecast/pyproject.toml`, `packages/forecast/src/norn_forecast/__init__.py`
- Create: `cli/pyproject.toml`, `cli/src/norn_cli/__init__.py`
- Test: `tests/test_imports.py`

- [ ] **Step 1: Write the failing test**

`tests/test_imports.py`:

```python
def test_packages_import():
    import norn_core
    import norn_integration
    import norn_forecast
    import norn_cli

    assert norn_core.__version__ == "0.0.0"
```

- [ ] **Step 2: Create the workspace root** `pyproject.toml`:

```toml
[project]
name = "norn"
version = "0.0.0"
requires-python = ">=3.12"
description = "norn — vendor-neutral forecasting layer (platform)"

[tool.uv.workspace]
members = ["packages/*", "cli"]

[tool.uv.sources]
norn-core = { workspace = true }
norn-integration = { workspace = true }
norn-forecast = { workspace = true }
norn-cli = { workspace = true }

[dependency-groups]
dev = ["pytest>=8.0"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 3: Create `packages/core/pyproject.toml`:**

```toml
[project]
name = "norn-core"
version = "0.0.0"
requires-python = ">=3.12"
dependencies = ["pydantic>=2.6", "pyyaml>=6.0", "clickhouse-connect>=0.7"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/norn_core"]
```

`packages/core/src/norn_core/__init__.py`:

```python
__version__ = "0.0.0"
```

- [ ] **Step 4: Create the other three packages.**

`packages/integration/pyproject.toml`:

```toml
[project]
name = "norn-integration"
version = "0.0.0"
requires-python = ">=3.12"
dependencies = ["norn-core"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/norn_integration"]

[tool.hatch.build.targets.wheel.force-include]
"src/norn_integration/schema.sql" = "norn_integration/schema.sql"
```

`packages/forecast/pyproject.toml`:

```toml
[project]
name = "norn-forecast"
version = "0.0.0"
requires-python = ">=3.12"
dependencies = ["norn-core", "norn-integration", "numpy>=1.26"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/norn_forecast"]
```

`cli/pyproject.toml`:

```toml
[project]
name = "norn-cli"
version = "0.0.0"
requires-python = ">=3.12"
dependencies = ["norn-core", "norn-integration", "norn-forecast", "typer>=0.12"]

[project.scripts]
norn = "norn_cli.main:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/norn_cli"]
```

Create empty `__init__.py` for `norn_integration`, `norn_forecast`; and `norn_cli/__init__.py` with `__version__ = "0.0.0"`.

- [ ] **Step 5: Sync and run the test**

Run: `uv sync && uv run pytest tests/test_imports.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml packages cli tests/test_imports.py
git commit -m "chore: scaffold norn uv workspace (core/integration/forecast/cli)"
```

---

### Task 2: Core contract — `ForecastJob`, `ForecastPoint`, `Grain`

**Files:**
- Create: `packages/core/src/norn_core/contract.py`
- Create: `forecasts/example.yml`
- Test: `tests/test_contract.py`

- [ ] **Step 1: Write the failing test**

`tests/test_contract.py`:

```python
from datetime import datetime
from pathlib import Path

from norn_core.contract import ForecastJob, ForecastPoint, Grain


def test_forecast_job_defaults():
    job = ForecastJob(metric="sales", source="analytics.mart_metric")
    assert job.grain is Grain.daily
    assert job.horizon == 30
    assert job.context_length == 512
    assert job.seasonality == 7
    assert job.dimensions == []
    assert job.model == "baseline-seasonal-naive"


def test_forecast_job_from_yaml(tmp_path: Path):
    p = tmp_path / "job.yml"
    p.write_text(
        "metric: sales\n"
        "source: analytics.mart_metric\n"
        "grain: daily\n"
        "dimensions: [region]\n"
        "horizon: 7\n"
        "seasonality: 7\n"
    )
    job = ForecastJob.from_yaml(p)
    assert job.metric == "sales"
    assert job.dimensions == ["region"]
    assert job.horizon == 7


def test_forecast_point_roundtrip():
    pt = ForecastPoint(
        forecast_run_id="run-1",
        metric_name="sales",
        segment_key="region=eu",
        forecast_ts=datetime(2026, 6, 1),
        horizon_step=1,
        y_hat=10.0,
        p10=8.0,
        p50=10.0,
        p90=12.0,
        model_name="baseline-seasonal-naive",
        created_at=datetime(2026, 5, 29),
    )
    assert pt.y_actual is None
    assert pt.p90 > pt.p10
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_contract.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'norn_core.contract'`

- [ ] **Step 3: Write minimal implementation**

`packages/core/src/norn_core/contract.py`:

```python
from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class Grain(str, Enum):
    hourly = "hourly"
    daily = "daily"


class ForecastJob(BaseModel):
    metric: str
    source: str  # ClickHouse table, e.g. "analytics.mart_metric"
    grain: Grain = Grain.daily
    dimensions: list[str] = Field(default_factory=list)
    horizon: int = 30
    context_length: int = 512
    seasonality: int = 7
    model: str = "baseline-seasonal-naive"
    schedule: str | None = None

    @classmethod
    def from_yaml(cls, path: str | Path) -> "ForecastJob":
        data = yaml.safe_load(Path(path).read_text())
        return cls.model_validate(data)


class ForecastPoint(BaseModel):
    forecast_run_id: str
    metric_name: str
    segment_key: str
    forecast_ts: datetime
    horizon_step: int
    y_hat: float
    p10: float
    p50: float
    p90: float
    y_actual: float | None = None
    model_name: str
    created_at: datetime
```

- [ ] **Step 4: Create the generic example job** `forecasts/example.yml`:

```yaml
# Generic example forecast-job (NOT crypto — crypto jobs live in norn-crypto-instance).
metric: sales
source: analytics.mart_metric
grain: daily
dimensions: [region]
horizon: 14
context_length: 180
seasonality: 7
model: baseline-seasonal-naive
schedule: "0 6 * * *"
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_contract.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add packages/core/src/norn_core/contract.py forecasts/example.yml tests/test_contract.py
git commit -m "feat(core): forecast-job + forecast-point contract with YAML loader"
```

---

### Task 3: ClickHouse client factory

**Files:**
- Create: `packages/core/src/norn_core/clickhouse.py`
- Test: `tests/test_clickhouse.py`

- [ ] **Step 1: Write the failing test**

`tests/test_clickhouse.py`:

```python
import pytest

from norn_core.clickhouse import parse_dsn


def test_parse_dsn_full():
    cfg = parse_dsn("http://norn:secret@db.example.com:8123/analytics")
    assert cfg == {
        "host": "db.example.com",
        "port": 8123,
        "username": "norn",
        "password": "secret",
        "database": "analytics",
        "secure": False,
    }


def test_parse_dsn_https_default_port():
    cfg = parse_dsn("https://user:pw@host/db")
    assert cfg["port"] == 8443
    assert cfg["secure"] is True


def test_parse_dsn_requires_database():
    with pytest.raises(ValueError):
        parse_dsn("http://user:pw@host:8123/")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_clickhouse.py -v`
Expected: FAIL with `ImportError: cannot import name 'parse_dsn'`

- [ ] **Step 3: Write minimal implementation**

`packages/core/src/norn_core/clickhouse.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_clickhouse.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/norn_core/clickhouse.py tests/test_clickhouse.py
git commit -m "feat(core): ClickHouse DSN parser + client factory"
```

---

### Task 4: Forecast-contract schema (idempotent DDL)

**Files:**
- Create: `packages/integration/src/norn_integration/schema.sql`
- Create: `packages/integration/src/norn_integration/schema.py`
- Create: `deploy/docker-compose.yml`
- Test: `tests/conftest.py`, `tests/test_schema.py`

- [ ] **Step 1: Write the ClickHouse compose service** `deploy/docker-compose.yml`:

```yaml
services:
  clickhouse:
    image: clickhouse/clickhouse-server:24.8
    container_name: norn-clickhouse
    ports:
      - "8123:8123"
      - "9000:9000"
    environment:
      CLICKHOUSE_DB: norn
      CLICKHOUSE_USER: norn
      CLICKHOUSE_PASSWORD: norn
      CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT: "1"
    ulimits:
      nofile:
        soft: 262144
        hard: 262144
    volumes:
      - clickhouse-data:/var/lib/clickhouse

volumes:
  clickhouse-data: {}
```

Bring it up (prerequisite for DB tests):

Run: `docker compose -f deploy/docker-compose.yml up -d clickhouse`
Expected: container `norn-clickhouse` healthy; `curl -s 'http://norn:norn@localhost:8123/?query=SELECT%201'` prints `1`.

- [ ] **Step 2: Write the session fixture** `tests/conftest.py`:

```python
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
```

- [ ] **Step 3: Write the failing test** `tests/test_schema.py`:

```python
def test_apply_schema_is_idempotent(ch):
    from norn_integration.schema import apply_schema

    apply_schema(ch)  # second apply must not raise
    tables = {row[0] for row in ch.query("SHOW TABLES").result_rows}
    assert {"forecast_run", "forecast_point"} <= tables


def test_forecast_point_columns(ch):
    cols = {row[0] for row in ch.query("DESCRIBE TABLE forecast_point").result_rows}
    assert {
        "forecast_run_id", "metric_name", "segment_key", "forecast_ts",
        "horizon_step", "y_hat", "p10", "p50", "p90", "y_actual",
        "model_name", "created_at",
    } <= cols
```

- [ ] **Step 4: Run test to verify it fails**

Run: `uv run pytest tests/test_schema.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'norn_integration.schema'`

- [ ] **Step 5: Write the DDL** `packages/integration/src/norn_integration/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS forecast_run (
    forecast_run_id String,
    forecast_job    String,
    status          String,
    model_name      String,
    model_version   String,
    started_at      DateTime,
    finished_at     Nullable(DateTime),
    segments_total  UInt32,
    segments_skipped UInt32,
    error           Nullable(String)
) ENGINE = MergeTree ORDER BY (forecast_run_id, started_at);

CREATE TABLE IF NOT EXISTS forecast_point (
    forecast_run_id String,
    metric_name     String,
    segment_key     String,
    forecast_ts     DateTime,
    horizon_step    UInt16,
    y_hat           Float64,
    p10             Float64,
    p50             Float64,
    p90             Float64,
    y_actual        Nullable(Float64),
    model_name      String,
    created_at      DateTime DEFAULT now()
) ENGINE = MergeTree ORDER BY (metric_name, segment_key, forecast_ts);
```

- [ ] **Step 6: Write the apply function** `packages/integration/src/norn_integration/schema.py`:

```python
from __future__ import annotations

from importlib.resources import files

from clickhouse_connect.driver.client import Client


def schema_sql() -> str:
    return files("norn_integration").joinpath("schema.sql").read_text()


def apply_schema(client: Client) -> None:
    for stmt in (s.strip() for s in schema_sql().split(";")):
        if stmt:
            client.command(stmt)
```

- [ ] **Step 7: Run test to verify it passes**

Run: `uv run pytest tests/test_schema.py -v`
Expected: PASS (2 tests)

- [ ] **Step 8: Commit**

```bash
git add deploy/docker-compose.yml packages/integration/src/norn_integration/schema.sql packages/integration/src/norn_integration/schema.py tests/conftest.py tests/test_schema.py
git commit -m "feat(integration): forecast-contract DDL + idempotent schema apply + CH compose"
```

---

### Task 5: Baseline forecaster (pure, seasonal-naive with intervals)

**Files:**
- Create: `packages/forecast/src/norn_forecast/baseline.py`
- Test: `tests/test_baseline.py`

- [ ] **Step 1: Write the failing test** `tests/test_baseline.py`:

```python
from norn_forecast.baseline import seasonal_naive_forecast


def test_perfect_seasonal_series_has_zero_spread():
    # 4 weeks of a clean weekly pattern; seasonal-naive should reproduce it exactly
    values = [1, 2, 3, 4, 5, 6, 7] * 4
    out = seasonal_naive_forecast(values, horizon=7, seasonality=7)
    assert len(out) == 7
    # next day after ...,7 is 1 (start of the weekly cycle)
    assert out[0]["y_hat"] == 1.0
    assert out[0]["p50"] == 1.0
    assert out[0]["p10"] == out[0]["p90"] == 1.0  # zero residual -> zero spread


def test_intervals_widen_with_horizon():
    values = [float(v) for v in [10, 12, 9, 11, 13, 8, 10] * 6]
    out = seasonal_naive_forecast(values, horizon=14, seasonality=7)
    width_first = out[0]["p90"] - out[0]["p10"]
    width_last = out[-1]["p90"] - out[-1]["p10"]
    assert width_last >= width_first > 0
    for row in out:
        assert row["p10"] <= row["p50"] <= row["p90"]


def test_short_series_falls_back_without_error():
    out = seasonal_naive_forecast([5.0, 6.0, 7.0], horizon=3, seasonality=7)
    assert len(out) == 3
    assert all(r["y_hat"] == 7.0 for r in out)  # last value carried forward
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_baseline.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'norn_forecast.baseline'`

- [ ] **Step 3: Write minimal implementation** `packages/forecast/src/norn_forecast/baseline.py`:

```python
from __future__ import annotations

import numpy as np

Z_80 = 1.2816  # z-score for an 80% interval (p10..p90)


def seasonal_naive_forecast(
    values: list[float], horizon: int, seasonality: int = 7
) -> list[dict]:
    arr = np.asarray(values, dtype=float)
    n = arr.size
    if n == 0:
        raise ValueError("values must be non-empty")

    if n > seasonality:
        resid = arr[seasonality:] - arr[:-seasonality]
        sigma = float(np.std(resid)) if resid.size else 0.0
    else:
        sigma = 0.0  # too short to estimate seasonal residuals

    out: list[dict] = []
    for h in range(1, horizon + 1):
        if n >= seasonality:
            idx = n - seasonality + ((h - 1) % seasonality)
            base = float(arr[idx])
        else:
            base = float(arr[-1])  # fallback: carry last value forward
        cycles = (h - 1) // seasonality + 1
        spread = sigma * np.sqrt(cycles)
        out.append(
            {
                "horizon_step": h,
                "y_hat": base,
                "p50": base,
                "p10": base - Z_80 * spread,
                "p90": base + Z_80 * spread,
            }
        )
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_baseline.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add packages/forecast/src/norn_forecast/baseline.py tests/test_baseline.py
git commit -m "feat(forecast): seasonal-naive baseline with empirical 80% intervals"
```

---

### Task 6: Forecast runner — extract → forecast → write-back

**Files:**
- Create: `packages/forecast/src/norn_forecast/runner.py`
- Test: `tests/test_runner.py`

- [ ] **Step 1: Write the failing test** `tests/test_runner.py`:

```python
from datetime import datetime, timedelta

from norn_core.contract import ForecastJob
from norn_forecast.runner import run_job


def _seed_mart(ch, rows):
    ch.command(
        "CREATE TABLE test_mart (ts DateTime, region String, value Float64) "
        "ENGINE = MergeTree ORDER BY (region, ts)"
    )
    ch.insert("test_mart", rows, column_names=["ts", "region", "value"])


def test_run_job_writes_points_per_segment(ch):
    start = datetime(2026, 1, 1)
    rows = []
    for d in range(28):  # 4 weeks
        ts = start + timedelta(days=d)
        rows.append([ts, "eu", float(d % 7 + 1)])
        rows.append([ts, "us", float((d % 7 + 1) * 10)])
    _seed_mart(ch, rows)

    job = ForecastJob(
        metric="value",
        source="test_mart",
        dimensions=["region"],
        horizon=7,
        seasonality=7,
    )
    run_id = run_job(job, client=ch)

    res = ch.query(
        "SELECT segment_key, count() FROM forecast_point "
        "WHERE forecast_run_id = %(r)s GROUP BY segment_key ORDER BY segment_key",
        parameters={"r": run_id},
    ).result_rows
    assert res == [("region=eu", 7), ("region=us", 7)]

    run = ch.query(
        "SELECT status, segments_total FROM forecast_run WHERE forecast_run_id = %(r)s",
        parameters={"r": run_id},
    ).result_rows
    assert run == [("success", 2)]


def test_run_job_no_dimensions_single_segment(ch):
    start = datetime(2026, 1, 1)
    rows = [[start + timedelta(days=d), "x", float(d % 7)] for d in range(21)]
    _seed_mart(ch, rows)

    job = ForecastJob(metric="value", source="test_mart", horizon=3, seasonality=7)
    run_id = run_job(job, client=ch)
    seg = ch.query(
        "SELECT DISTINCT segment_key FROM forecast_point WHERE forecast_run_id=%(r)s",
        parameters={"r": run_id},
    ).result_rows
    assert seg == [("all",)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'norn_forecast.runner'`

- [ ] **Step 3: Write minimal implementation** `packages/forecast/src/norn_forecast/runner.py`:

```python
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from clickhouse_connect.driver.client import Client

from norn_core.contract import ForecastJob, Grain
from norn_forecast.baseline import seasonal_naive_forecast

_STEP = {Grain.daily: timedelta(days=1), Grain.hourly: timedelta(hours=1)}


def _segments(client: Client, job: ForecastJob) -> list[dict]:
    if not job.dimensions:
        return [{}]
    cols = ", ".join(job.dimensions)
    rows = client.query(
        f"SELECT DISTINCT {cols} FROM {job.source} ORDER BY {cols}"
    ).result_rows
    return [dict(zip(job.dimensions, r)) for r in rows]


def _segment_key(dims: dict) -> str:
    if not dims:
        return "all"
    return "|".join(f"{k}={dims[k]}" for k in dims)


def _series(client: Client, job: ForecastJob, dims: dict) -> tuple[list[datetime], list[float]]:
    where = " AND ".join(f"{k} = %({k})s" for k in dims) or "1 = 1"
    rows = client.query(
        f"SELECT ts, {job.metric} FROM {job.source} WHERE {where} "
        f"ORDER BY ts LIMIT {job.context_length}",
        parameters=dims,
    ).result_rows
    ts = [r[0] for r in rows]
    vals = [float(r[1]) for r in rows]
    return ts, vals


def run_job(job: ForecastJob, client: Client) -> str:
    run_id = str(uuid.uuid4())
    started = datetime.utcnow()
    step = _STEP[job.grain]
    segments = _segments(client, job)
    points: list[list] = []

    for dims in segments:
        ts, vals = _series(client, job, dims)
        if not vals:
            continue
        seg_key = _segment_key(dims)
        last_ts = ts[-1]
        fc = seasonal_naive_forecast(vals, job.horizon, job.seasonality)
        now = datetime.utcnow()
        for row in fc:
            points.append([
                run_id, job.metric, seg_key,
                last_ts + step * row["horizon_step"],
                row["horizon_step"], row["y_hat"],
                row["p10"], row["p50"], row["p90"],
                None, job.model, now,
            ])

    if points:
        client.insert(
            "forecast_point", points,
            column_names=[
                "forecast_run_id", "metric_name", "segment_key", "forecast_ts",
                "horizon_step", "y_hat", "p10", "p50", "p90", "y_actual",
                "model_name", "created_at",
            ],
        )

    client.insert(
        "forecast_run",
        [[run_id, job.metric, "success", job.model, "v0",
          started, datetime.utcnow(), len(segments), 0, None]],
        column_names=[
            "forecast_run_id", "forecast_job", "status", "model_name", "model_version",
            "started_at", "finished_at", "segments_total", "segments_skipped", "error",
        ],
    )
    return run_id
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_runner.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add packages/forecast/src/norn_forecast/runner.py tests/test_runner.py
git commit -m "feat(forecast): runner — extract series, forecast per segment, write contract rows"
```

---

### Task 7: CLI — `norn schema-apply`, `norn forecast`, `norn up`

**Files:**
- Create: `cli/src/norn_cli/main.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test** `tests/test_cli.py`:

```python
from typer.testing import CliRunner

from norn_cli.main import app

runner = CliRunner()


def test_forecast_command_runs_and_prints_run_id(ch, tmp_path, monkeypatch):
    from datetime import datetime, timedelta

    ch.command(
        "CREATE TABLE test_mart (ts DateTime, region String, value Float64) "
        "ENGINE = MergeTree ORDER BY (region, ts)"
    )
    start = datetime(2026, 1, 1)
    ch.insert(
        "test_mart",
        [[start + timedelta(days=d), "eu", float(d % 7)] for d in range(21)],
        column_names=["ts", "region", "value"],
    )
    monkeypatch.setenv("NORN_CLICKHOUSE_URL", "http://norn:norn@localhost:8123/norn")

    job = tmp_path / "job.yml"
    job.write_text(
        "metric: value\nsource: test_mart\ndimensions: [region]\nhorizon: 5\nseasonality: 7\n"
    )
    result = runner.invoke(app, ["forecast", str(job)])
    assert result.exit_code == 0, result.output
    assert "run_id=" in result.output


def test_schema_apply_command(ch, monkeypatch):
    monkeypatch.setenv("NORN_CLICKHOUSE_URL", "http://norn:norn@localhost:8123/norn")
    result = runner.invoke(app, ["schema-apply"])
    assert result.exit_code == 0, result.output
    assert "schema applied" in result.output.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'norn_cli.main'`

- [ ] **Step 3: Write minimal implementation** `cli/src/norn_cli/main.py`:

```python
from __future__ import annotations

import subprocess
from pathlib import Path

import typer

from norn_core.clickhouse import get_client
from norn_core.contract import ForecastJob
from norn_forecast.runner import run_job
from norn_integration.schema import apply_schema

app = typer.Typer(help="norn — vendor-neutral forecasting layer")

COMPOSE = Path(__file__).resolve().parents[3] / "deploy" / "docker-compose.yml"


@app.command("schema-apply")
def schema_apply() -> None:
    """Apply the forecast-contract schema to ClickHouse (idempotent)."""
    client = get_client()
    apply_schema(client)
    typer.echo("schema applied")


@app.command()
def forecast(job_path: str = typer.Argument(..., help="path to a forecast-job YAML")) -> None:
    """Run a forecast job: extract -> forecast -> write contract rows."""
    job = ForecastJob.from_yaml(job_path)
    client = get_client()
    apply_schema(client)
    run_id = run_job(job, client=client)
    typer.echo(f"run_id={run_id}")


@app.command()
def up() -> None:
    """Bring up the local sidecar (ClickHouse) in Docker and apply the schema."""
    subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), "up", "-d", "clickhouse"], check=True
    )
    typer.echo("clickhouse up; run `norn schema-apply` once it is healthy")


if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -v`
Expected: PASS (all tasks' tests green)

- [ ] **Step 6: Commit**

```bash
git add cli/src/norn_cli/main.py tests/test_cli.py
git commit -m "feat(cli): norn up / schema-apply / forecast commands"
```

---

### Task 8: Lightdash integration notes + README (instructions only, no Lightdash code)

**Files:**
- Create: `docs/integration/lightdash.md`
- Modify: `README.md`

- [ ] **Step 1: Write the integration instructions** `docs/integration/lightdash.md`:

```markdown
# Integrating norn with Lightdash

norn does **not** ship or fork Lightdash. norn writes forecast rows to ClickHouse
(`forecast_point`); you point your existing Lightdash project at those tables.

## Steps
1. In your dbt project add a model `actual_vs_forecast` that joins your metric mart
   to `forecast_point` on `(metric_name, segment_key, ts = forecast_ts)`.
2. Expose `y_actual`, `y_hat`, `p10`, `p90`, `error_abs` as Lightdash metrics.
3. Build a chart: actual line + forecast line + p10/p90 band per segment.

## Connection
Lightdash connects to the **same ClickHouse** norn writes to. norn never touches
Lightdash's Postgres. Crypto-specific dashboards live in `norn-crypto-instance`,
not in this repo.
```

- [ ] **Step 2: Replace the README** `README.md`:

```markdown
# norn

Vendor-neutral forecasting layer: `dbt → ClickHouse → forecast worker → Lightdash`,
plus an MCP interface for agents. This repo is the **generic platform** — domain
instances (e.g. crypto) live in linked submodule repos.

## Quickstart (local, one command)

```bash
uv sync
uv run norn up            # ClickHouse in Docker
uv run norn schema-apply  # forecast-contract tables
uv run norn forecast forecasts/example.yml
```

## Layout
- `packages/core` — contract (forecast-job, forecast-point) + ClickHouse client
- `packages/integration` — ClickHouse DDL + (later) dbt/Lightdash glue
- `packages/forecast` — forecaster (baseline now; TimesFM in Plan 2) + runner
- `cli` — `norn` entrypoint
- `deploy/docker-compose.yml` — local sidecar

## Tests
Requires a local ClickHouse: `docker compose -f deploy/docker-compose.yml up -d clickhouse`,
then `uv run pytest`.
```

- [ ] **Step 3: Commit**

```bash
git add docs/integration/lightdash.md README.md
git commit -m "docs: Lightdash integration notes (instructions only) + README quickstart"
```

---

## Subsequent Plans (roadmap — each its own spec→plan cycle)

These are **not** tasks in this plan; they are the next plans, in order. Each produces working, testable software and depends only on the ones before it.

- **Plan 2 — TimesFM worker.** Replace the baseline behind a stable `Forecaster` interface (`forecast(values, horizon) -> rows`). Pin the worker container to Python 3.12/3.13 + torch + `timesfm` (monorepo-doc §5). Add the three products (price path, vol/range, long-horizon job). Calibration backtest (rolling-origin, p10/p90 coverage vs nominal) writing `forecast_segment` (wape/mape/coverage).
- **Plan 3 — Serve layer.** FastAPI + MCP server in `packages/forecast` exposing `get_forecast`, `get_expected_range`, `check_ladder_rungs`, `get_divergence`, `get_calibration`; reading `forecast_point`/`forecast_run`. Graceful degradation contract. Add MCP server to compose.
- **Plan 4 — dbt + Lightdash glue.** `packages/integration` reads dbt `manifest.json`, generates the `actual_vs_forecast` dbt model, registers forecast outputs. (No Lightdash code — adapter + docs only.)
- **Plan 5 — Dependency agent.** `packages/agent`: lagged-corr / Granger / mutual-info between metric series → `get_dependencies` ("BTC leads TON by N days") with caveats.
- **Plan 6 — `norn-crypto-instance` (separate repo, submodule).** Bybit→ClickHouse ingestion (CoinGecko fallback), BTC/TON `mart_metric` dbt models, crypto forecast-job YAMLs, crypto Lightdash dashboards. Linked to norn as a git submodule.
- **Plan 7 — pibitagent consumption (in pibitagent repo).** Register norn MCP server with `pi`; fold forecast+calibration+dependencies into `analysis_context.json`; ladder sanity-check; Telegram divergence alerts via the existing alert path.
