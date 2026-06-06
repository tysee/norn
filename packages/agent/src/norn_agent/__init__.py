"""
packages/agent/src/norn_agent/__init__.py

Agent package for analyzing dependencies between metrics on the norn platform. Purpose:
to connect the mart layer (mart_metric) with an LLM judgment about lead/lag dependencies.
The statistical methods (cross-correlation, Granger) compute the evidence, and the
PydanticAI agent decides whether a dependency is real and explains it to a human.
The platform is domain-neutral: the metric and segments are set by the caller.

Package contents:
- agent — building the LLM agent and issuing a structured decision from the evidence.
- analyze — orchestration of a pass: reading the series, running the methods, writing the results.
- contract — Pydantic models of the contract (job, method measurement, agent decision).
- methods — registry of the statistical evidence methods.
"""

__version__ = "0.0.0"
