"""The norn_cli package — the command-line interface of the norn platform.

Provides a single CLI entry point (typer) that ties together norn's
subsystems: the ClickHouse store, the contract schema, forecasting,
calibration, dependency analysis and the MCP server. The commands are
implemented in the ``main`` module.
"""

__version__ = "0.0.0"
