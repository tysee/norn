"""
packages/scheduler/src/norn_scheduler/manifest.py

jobs.yml manifest contract for the built-in scheduler: which norn jobs to run,
on what cron schedule and with what retries. The manifest is the single source
of the schedule (schedule: in the job YAML stays a hint and is ignored here).

Classes:
- ManifestJob — one entry: name/action/job/schedule (+ retries/enabled).
- SchedulerManifest — list of entries; from_yaml() with fail-fast validation
  (unique names, valid cron, known action).
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from apscheduler.triggers.cron import CronTrigger
from pydantic import BaseModel, field_validator, model_validator


class ManifestJob(BaseModel):
    name: str
    action: Literal["forecast", "calibrate", "deps"]
    job: str                      # path to an existing job YAML (ForecastJob/DependencyJob)
    schedule: str                 # cron (5 fields); manifest overrides the hint in the job YAML
    retries: int | None = None    # None -> default from config/scheduler.yml
    enabled: bool = True

    @field_validator("schedule")
    @classmethod
    def _valid_cron(cls, v: str) -> str:
        try:
            CronTrigger.from_crontab(v)
        except ValueError as e:
            raise ValueError(f"invalid cron expression {v!r}: {e}") from e
        return v


class SchedulerManifest(BaseModel):
    jobs: list[ManifestJob]

    @model_validator(mode="after")
    def _unique_names(self) -> "SchedulerManifest":
        seen: set[str] = set()
        for j in self.jobs:
            if j.name in seen:
                raise ValueError(f"duplicate job name: {j.name!r}")
            seen.add(j.name)
        return self

    def enabled_jobs(self) -> list[ManifestJob]:
        return [j for j in self.jobs if j.enabled]

    @classmethod
    def from_yaml(cls, path: str | Path) -> "SchedulerManifest":
        return cls.model_validate(yaml.safe_load(Path(path).read_text()))
