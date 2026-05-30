"""
packages/agent/src/norn_agent/agent.py

LLM-уровень слоя зависимостей: PydanticAI-агент превращает статистические улики
методов в структурированное решение «зависимость реальна или ложная» с
объяснением и калибровкой уверенности. Здесь живут системный промпт (правила
суждения, в т.ч. оговорка «корреляция != причинность» и сравнение с прошлым
прогоном) и тонкая обёртка вокруг Agent.run_sync. Модель берётся из конфига norn.

Публичные функции:
- build_agent(model=None) -> Agent — собирает агента с output_type=DependencyDecision
  и системным промптом; модель по умолчанию читается из настроек платформы.
- judge_dependencies(measurements, meta, prior_measurements=None, agent=None)
  -> DependencyDecision — формирует промпт из текущих (и опционально прошлых)
  улик и возвращает решение агента по каждой зависимости.
"""
from __future__ import annotations

import json

from pydantic_ai import Agent

from norn_agent.contract import DependencyDecision, DependencyMeasurement

SYSTEM_PROMPT = (
    "You are a disciplined analyst of lead/lag dependencies between metric time series. "
    "You receive statistical evidence (lagged cross-correlation and Granger causality) "
    "computed on the caller-provided (ideally stationary) metric series. Decide whether each dependency is REAL or "
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
    # --- выбор модели: явный аргумент либо значение из конфига платформы ---
    if model is None:
        from norn_core.config import get_settings

        model = get_settings().agent.model
    return Agent(model, output_type=DependencyDecision, instructions=SYSTEM_PROMPT)


def judge_dependencies(
    measurements: list[DependencyMeasurement],
    meta: dict,
    prior_measurements: list[DependencyMeasurement] | None = None,
    agent: Agent | None = None,
) -> DependencyDecision:
    agent = agent or build_agent()
    # --- собрать промпт: шапка с сегментами/метрикой + текущие улики методов ---
    prompt = (
        f"Segments: source={meta['source_segment']} target={meta['target_segment']} "
        f"metric={meta['metric_name']}.\nCurrent evidence:\n"
        + json.dumps([m.model_dump() for m in measurements], indent=2)
    )
    # --- добавить улики прошлого прогона для оценки дрейфа зависимости ---
    if prior_measurements:
        prompt += "\nPrior evidence (previous run):\n" + json.dumps(
            [m.model_dump() for m in prior_measurements], indent=2
        )
    # --- синхронный вызов агента -> структурированное решение ---
    return agent.run_sync(prompt).output
