"""
packages/agent/src/norn_agent/contract.py

Типизированный контракт слоя зависимостей платформы norn — единый словарь данных
между оркестратором, статистическими методами и LLM-агентом. Pydantic-модели здесь
описывают вход (что анализировать), промежуточные улики методов и структурированный
выход агента, гарантируя совместимость со схемой ClickHouse. Доменно-нейтрально:
конкретная метрика и сегменты приходят извне.

Публичные классы:
- DependencyJob — конфиг анализа (метрика, mart, два сегмента, max_lag,
  context_length, methods); .from_yaml загружает job из YAML, .resolved()
  доливает незаданные тюнинги из настроек платформы.
- DependencyMeasurement — одна улика метода: lag/score/direction/p_value/confidence.
- DependencyRelation — решение агента по одной зависимости (is_real, объяснение,
  оговорки, заметка о дрейфе относительно прошлого прогона).
- DependencyDecision — обёртка-список DependencyRelation, output_type агента.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel


class DependencyJob(BaseModel):
    source_segment: str
    target_segment: str
    metric: str  # domain metric_name (no default — platform is domain-agnostic)
    mart: str = "mart_metric"
    max_lag: int | None = None
    context_length: int | None = None
    methods: list[str] | None = None

    @classmethod
    def from_yaml(cls, path: str | Path) -> "DependencyJob":
        return cls.model_validate(yaml.safe_load(Path(path).read_text()))

    def resolved(self) -> "DependencyJob":
        """Fill unset tunables from the config layer (explicit job values win)."""
        from norn_core.config import get_settings

        a = get_settings().agent
        return self.model_copy(update={
            "max_lag": self.max_lag if self.max_lag is not None else a.max_lag,
            "context_length": self.context_length if self.context_length is not None else a.context_length,
            "methods": self.methods if self.methods is not None else list(a.methods),
        })


class DependencyMeasurement(BaseModel):
    method: str
    lag: int
    score: float
    direction: str
    p_value: float | None = None
    confidence: float


class DependencyRelation(BaseModel):
    source_segment: str
    target_segment: str
    metric_name: str
    lag: int
    direction: str
    is_real: bool
    confidence: float
    explanation: str
    caveats: str
    change_note: str = ""  # what changed vs the previous run (corr/lag/decision drift); "" if first run


class DependencyDecision(BaseModel):
    relations: list[DependencyRelation]
