"""
packages/agent/src/norn_agent/analyze.py

Orchestrator for a single dependency-analysis pass — the assembly point of the
layer in the norn platform. It wires together the metric mart (ClickHouse
mart_metric), the statistical methods and the LLM agent: it pulls the latest
series of two segments, aligns them on a common timeline, runs the evidence
methods, records the evidence in metric_dependency, and the agent's structured
decision — in dependency_explanation. It knows about the previous run so the
agent can assess dependency drift.

Public functions:
- analyze_dependencies(job, client, agent=None) -> AnalysisResult — performs a
  full pass for a single job. Statistics (metric_dependency) are always written;
  when the LLM is unavailable (LLMUnavailable) the explanation is skipped with an
  ERROR log and a full traceback, and the result explicitly reports the
  degradation (explained=False + reason).

Internal helpers:
- _prior_measurements — evidence from the most recent prior run for the same
  metric/source/target triple (for drift-aware judging).
- _series — reads the last context_length points of the series for (metric, segment).
- _align — aligns two series on common timestamps + the observation window.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from clickhouse_connect.driver.client import Client

from norn_core.clickhouse import _safe_identifier

from norn_agent.agent import LLMUnavailable, judge_dependencies
from norn_agent.contract import DependencyDecision, DependencyJob, DependencyMeasurement
from norn_agent.methods import METHODS

logger = logging.getLogger(__name__)


@dataclass
class AnalysisResult:
    run_id: str
    explained: bool
    degradation_reason: str | None = None


def _prior_measurements(client: Client, job: DependencyJob) -> list[DependencyMeasurement]:
    """Measurements from the most recent PRIOR run for this metric/source/target."""
    # --- find the id of the most recent prior run for this triple ---
    run = client.query(
        "SELECT analysis_run_id FROM metric_dependency "
        "WHERE metric_name=%(m)s AND source_segment=%(s)s AND target_segment=%(t)s "
        "ORDER BY created_at DESC LIMIT 1",
        parameters={"m": job.metric, "s": job.source_segment, "t": job.target_segment},
    ).result_rows
    if not run:
        return []
    # --- pull that run's evidence and rebuild the measurement models ---
    rows = client.query(
        "SELECT method, lag, score, direction, p_value, confidence FROM metric_dependency "
        "WHERE analysis_run_id=%(r)s",
        parameters={"r": run[0][0]},
    ).result_rows
    return [
        DependencyMeasurement(
            method=r[0], lag=r[1], score=r[2], direction=r[3], p_value=r[4], confidence=r[5]
        )
        for r in rows
    ]


def _series(client: Client, mart: str, metric: str, segment: str, context_length: int):
    mart = _safe_identifier(mart)
    rows = client.query(
        f"SELECT ts, value FROM (SELECT ts, value FROM {mart} "
        "WHERE metric_name=%(m)s AND segment_key=%(s)s ORDER BY ts DESC "
        f"LIMIT {context_length}) ORDER BY ts",
        parameters={"m": metric, "s": segment},
    ).result_rows
    return [r[0] for r in rows], [float(r[1]) for r in rows]


def _align(src_ts, src_vals, tgt_ts, tgt_vals):
    # --- index target by time to find common timestamps ---
    tmap = dict(zip(tgt_ts, tgt_vals))
    # --- intersection on ts, sorted by time ---
    common = sorted(
        ((ts, sv, tmap[ts]) for ts, sv in zip(src_ts, src_vals) if ts in tmap),
        key=lambda x: x[0],
    )
    # --- split into parallel series and compute the observation window ---
    src = [c[1] for c in common]
    tgt = [c[2] for c in common]
    window = (common[0][0], common[-1][0]) if common else (datetime(1970, 1, 1), datetime(1970, 1, 1))
    return src, tgt, window


def analyze_dependencies(job: DependencyJob, client: Client, agent=None) -> AnalysisResult:
    # --- fill in the job's unset tunables from config and mint a run id ---
    job = job.resolved()
    run_id = str(uuid.uuid4())
    # --- extract: read the series of both segments and align on common time ---
    s_ts, s_v = _series(client, job.mart, job.metric, job.source_segment, job.context_length)
    t_ts, t_v = _series(client, job.mart, job.metric, job.target_segment, job.context_length)
    src, tgt, (w0, w1) = _align(s_ts, s_v, t_ts, t_v)
    # Progress log: the deps pass lasts minutes (LLM judge); without milestones it looks hung.
    logger.info("run %s extract: %d aligned points (%s..%s) %s -> %s",
                run_id, len(src), w0, w1, job.source_segment, job.target_segment)

    # --- compute: run the selected evidence methods (granger gets its tunables from config) ---
    from norn_core.config import get_settings

    a = get_settings().agent
    measurements = []
    for name in job.methods:
        if name == "granger":
            measurements.append(
                METHODS[name](src, tgt, job.max_lag,
                               min_points_factor=a.granger_min_points_factor,
                               significance=a.granger_significance)
            )
        else:
            measurements.append(METHODS[name](src, tgt, job.max_lag))

    # Look up the previous run BEFORE inserting this run's rows (drift-aware judging).
    prior = _prior_measurements(client, job)

    # --- write-back: record the methods' evidence into metric_dependency ---
    dep_rows = [
        [run_id, job.metric, job.source_segment, job.target_segment, m.method,
         m.lag, m.score, m.direction, m.p_value, m.confidence, w0, w1, datetime.now(UTC)]
        for m in measurements
    ]
    client.insert(
        "metric_dependency", dep_rows,
        column_names=[
            "analysis_run_id", "metric_name", "source_segment", "target_segment",
            "method", "lag", "score", "direction", "p_value", "confidence",
            "window_start", "window_end", "created_at",
        ],
    )

    # --- judge: hand the evidence (and the prior one) to the agent for a reality decision ---
    meta = {
        "source_segment": job.source_segment,
        "target_segment": job.target_segment,
        "metric_name": job.metric,
    }
    logger.info("run %s judge: %d measurements via LLM %s/%s — local models can take minutes",
                run_id, len(measurements), a.provider, a.model)
    t0 = time.monotonic()
    try:
        decision = judge_dependencies(measurements, meta, prior_measurements=prior, agent=agent)
        explained, reason = True, None
        logger.info("run %s judge: done in %.1fs (relations=%d)",
                    run_id, time.monotonic() - t0, len(decision.relations))
    except LLMUnavailable as e:
        logger.error("LLM explanation skipped for run %s (provider=%s model=%s): %s",
                     run_id, a.provider, a.model, e, exc_info=True)
        decision, explained, reason = DependencyDecision(relations=[]), False, str(e)
    # --- provenance: the model that actually produced the verdict. In worker
    # mode the judge runs in another process with its OWN config, so a.model
    # (the client's) would be wrong — ask the worker (/health reports it).
    judge_model = a.model
    if explained and a.worker_url:
        try:
            import httpx

            h = httpx.get(f"{a.worker_url.rstrip('/')}/health", timeout=5.0).json()
            judge_model = h.get("model") or a.model
        except (httpx.HTTPError, ValueError):
            pass  # keep the local model as a best-effort fallback
    # --- write-back: save the agent's explanations into dependency_explanation ---
    # Segment keys are taken from the job (canonical 'symbol=...'), NOT from the LLM
    # response: the model often strips the 'symbol=' prefix, which makes the keys
    # diverge from metric_dependency and the LEFT join in get_dependencies miss
    # (E2E contract bug).
    exp_rows = [
        [run_id, job.metric, job.source_segment, job.target_segment, r.lag, r.direction,
         1 if r.is_real else 0, r.confidence, r.explanation, r.caveats, r.change_note,
         judge_model, datetime.now(UTC)]
        for r in decision.relations
    ]
    if exp_rows:
        client.insert(
            "dependency_explanation", exp_rows,
            column_names=[
                "analysis_run_id", "metric_name", "source_segment", "target_segment",
                "lag", "direction", "is_real", "confidence", "explanation", "caveats",
                "change_note", "llm_model", "created_at",
            ],
        )
    return AnalysisResult(run_id=run_id, explained=explained, degradation_reason=reason)
