"""
packages/core/src/norn_core/__init__.py

Core package of the norn platform: shared, worker-agnostic primitives
reused by all platform services. Here live the typed
config layer (YAML + env), the ClickHouse client factory and the data contracts
(forecast-job / forecast-point), which define a single exchange format between
the forecast-worker, the agent and the integration layer.
"""
__version__ = "0.0.0"
