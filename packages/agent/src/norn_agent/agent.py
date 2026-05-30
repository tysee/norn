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
import logging

from pydantic_ai import Agent

logger = logging.getLogger(__name__)

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


def _build_model(a):
    """Construct the pydantic-ai model for the configured provider (lazy SDK imports).

    Секреты — только из env (per-provider ключи), никаких хардкодов. Ничего не
    вызывает по сети: только конструирует объект модели/провайдера.
    """
    import os

    p = a.provider
    if p == "ollama":
        from pydantic_ai.models.ollama import OllamaModel
        from pydantic_ai.providers.ollama import OllamaProvider

        return OllamaModel(
            a.model,
            provider=OllamaProvider(base_url=a.base_url or "http://localhost:11434/v1"),
        )
    if p in ("openai-api", "openai-oauth"):
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider

        key_env = "OPENAI_API_KEY" if p == "openai-api" else "NORN_OPENAI_OAUTH_TOKEN"
        kwargs = {"api_key": os.environ[key_env]}
        if a.base_url:
            kwargs["base_url"] = a.base_url
        return OpenAIChatModel(a.model, provider=OpenAIProvider(**kwargs))
    if p == "openrouter":
        from pydantic_ai.models.openrouter import OpenRouterModel
        from pydantic_ai.providers.openrouter import OpenRouterProvider

        return OpenRouterModel(
            a.model, provider=OpenRouterProvider(api_key=os.environ["OPENROUTER_API_KEY"])
        )
    if p == "anthropic-api":
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider

        return AnthropicModel(
            a.model, provider=AnthropicProvider(api_key=os.environ["ANTHROPIC_API_KEY"])
        )
    raise ValueError(f"unknown agent.provider: {p!r}")


def build_agent(model=None) -> Agent:
    # --- явный override (в т.ч. TestModel в тестах) — собираем агента как есть ---
    if model is not None:
        return Agent(model, output_type=DependencyDecision, instructions=SYSTEM_PROMPT)
    # --- дефолт: строим модель-объект под провайдера из конфига платформы ---
    from norn_core.config import get_settings

    return Agent(
        _build_model(get_settings().agent),
        output_type=DependencyDecision,
        instructions=SYSTEM_PROMPT,
    )


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
    # Деградируем мягко: при сбое модели/транспорта возвращаем пустое решение,
    # чтобы analyze_dependencies всё равно записал числовые улики (metric_dependency)
    # и просто не создавал строк dependency_explanation.
    try:
        return agent.run_sync(prompt).output
    except Exception:
        logger.warning(
            "judge_dependencies: LLM call failed; degrading to empty decision",
            exc_info=False,
        )
        return DependencyDecision(relations=[])
