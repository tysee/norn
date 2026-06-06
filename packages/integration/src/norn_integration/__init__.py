"""
packages/integration/src/norn_integration/__init__.py

Integration package for the norn platform's external storage.

Responsible for the result-persistence layer: it connects domain pipelines
(forecasting, dependency analysis) with the ClickHouse analytical store.
This is where the DDL contract (schema.sql) lives, along with its idempotent
application to the cluster, so that the other packages can write and read
results against a single, stable table schema.
"""
