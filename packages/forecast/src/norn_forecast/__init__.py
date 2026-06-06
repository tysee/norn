"""
packages/forecast/src/norn_forecast/__init__.py

The norn_forecast package — the forecasting layer of the norn platform. Pulls per-segment
time series from the ClickHouse contract, builds quantile forecasts
(baseline seasonal-naive or TimesFM 2.5), computes rolling-origin calibration and
exposes the results to the agent through MCP tools on top of the contract tables.
"""
