"""
packages/agent/src/norn_agent/agent.py

PydanticAI-агент: по уликам методов решает реальность зависимости и объясняет её.

Методы:
- build_agent(model=None) -> Agent — агент с output_type=DependencyDecision.
- judge_dependencies(measurements, meta, agent=None) -> DependencyDecision.
"""
from __future__ import annotations

import json
import os

from pydantic_ai import Agent

from norn_agent.contract import DependencyDecision, DependencyMeasurement

SYSTEM_PROMPT = (
    "You are a disciplined analyst of lead/lag dependencies between metric time series. "
    "You receive statistical evidence (lagged cross-correlation and Granger causality) "
    "computed on stationary log-returns. Decide whether each dependency is REAL or "
    "spurious, judging by agreement between methods, Granger significance, and the "
    "plausibility of the lag. Explain briefly and always include the caveat that "
    "correlation is not causation. Calibrate confidence — when methods disagree or the "
    "signal is weak, lower it. Do not invent causal mechanisms you cannot support. "
    "If PRIOR evidence (from the previous analysis run) is provided, compare it to the "
    "current evidence and record what changed in 'change_note' (e.g. 'corr 0.8->0.4, "
    "lag 3->5, decision flipped real->spurious'); if the relationship became unstable, "
    "lower confidence. When there is no prior evidence, leave change_note empty."
)


def build_agent(model: str | None = None) -> Agent:
    model = model or os.environ.get("NORN_AGENT_MODEL", "anthropic:claude-sonnet-4-5")
    return Agent(model, output_type=DependencyDecision, system_prompt=SYSTEM_PROMPT)


def judge_dependencies(
    measurements: list[DependencyMeasurement],
    meta: dict,
    prior_measurements: list[DependencyMeasurement] | None = None,
    agent: Agent | None = None,
) -> DependencyDecision:
    agent = agent or build_agent()
    prompt = (
        f"Segments: source={meta['source_segment']} target={meta['target_segment']} "
        f"metric={meta['metric_name']}.\nCurrent evidence:\n"
        + json.dumps([m.model_dump() for m in measurements], indent=2)
    )
    if prior_measurements:
        prompt += "\nPrior evidence (previous run):\n" + json.dumps(
            [m.model_dump() for m in prior_measurements], indent=2
        )
    return agent.run_sync(prompt).output
