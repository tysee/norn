"""
packages/agent/src/norn_agent/contract.py

Контракт слоя зависимостей: конфиг job, измерение-улика метода и решение агента.

Классы:
- DependencyJob — конфиг анализа (метрика, mart, два сегмента, max_lag); .from_yaml.
- DependencyMeasurement — улика одного метода (lag/score/direction/p_value/confidence).
- DependencyRelation / DependencyDecision — структурированный вывод LLM-агента.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class DependencyJob(BaseModel):
    source_segment: str
    target_segment: str
    metric: str  # domain metric_name (no default — platform is domain-agnostic)
    mart: str = "mart_metric"
    max_lag: int = 10
    context_length: int = 512
    methods: list[str] = Field(
        default_factory=lambda: ["lagged_cross_correlation", "granger"]
    )

    @classmethod
    def from_yaml(cls, path: str | Path) -> "DependencyJob":
        return cls.model_validate(yaml.safe_load(Path(path).read_text()))


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
