"""
packages/agent/src/norn_agent/agent.py

LLM layer of the dependency subsystem: a PydanticAI agent turns the methods'
statistical evidence into a structured "dependency is real or spurious" decision
with an explanation and calibrated confidence. This is where the system prompt
lives (judging rules, including the "correlation != causation" caveat and the
comparison against the previous run) and a thin wrapper around Agent.run_sync.
The model is taken from the norn config.

Public functions:
- build_agent(model=None) -> Agent — assembles an agent with
  output_type=DependencyDecision and the system prompt; the default model is read
  from the platform settings.
- judge_dependencies(measurements, meta, prior_measurements=None, agent=None)
  -> DependencyDecision — builds a prompt from the current (and optionally prior)
  evidence and returns the agent's decision for each dependency. On a known
  infrastructure failure (missing credential / invalid config / model or
  transport error) it raises LLMUnavailable — no silent degradation. It may call
  out to the agent worker (`agent.worker_url`); a worker failure == LLMUnavailable.
"""
from __future__ import annotations

import json
import logging
import threading

from pydantic_ai import Agent

from norn_agent.contract import DependencyDecision, DependencyMeasurement

logger = logging.getLogger(__name__)


class LLMUnavailable(RuntimeError):
    """The LLM/provider is unavailable or returned an invalid response — the dependency explanation is skipped."""


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

    Secrets come only from env (per-provider keys), no hardcoding. Nothing is
    called over the network: it only constructs the model/provider object.
    """
    import os

    p = a.provider
    if p == "ollama":
        from pydantic_ai.models.ollama import OllamaModel
        from pydantic_ai.providers.ollama import OllamaProvider

        if not a.base_url:
            raise ValueError("agent.base_url is required for the ollama provider")
        return OllamaModel(a.model, provider=OllamaProvider(base_url=a.base_url))
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


def _output_type(a):
    """The structured-output mode is taken from config (agent.output_mode), not hardcoded per provider."""
    m = a.output_mode
    if m == "tool":
        return DependencyDecision
    if m == "native":
        from pydantic_ai import NativeOutput

        return NativeOutput(DependencyDecision)
    if m == "prompted":
        from pydantic_ai import PromptedOutput

        return PromptedOutput(DependencyDecision)
    raise ValueError(f"unknown agent.output_mode: {m!r}")


def build_agent(model=None) -> Agent:
    # --- explicit override (incl. TestModel in tests) — assemble the agent as-is ---
    if model is not None:
        return Agent(model, output_type=DependencyDecision, instructions=SYSTEM_PROMPT)
    # --- default: build the model object and output mode for the provider from config ---
    from norn_core.config import get_settings

    a = get_settings().agent
    return Agent(
        _build_model(a),
        output_type=_output_type(a),
        instructions=SYSTEM_PROMPT,
    )


# Process-lifetime HTTP client for agent-worker calls: one connection pool
# instead of a new TCP connection per judge/health request. Never closed —
# it lives as long as the process (scheduler / CLI one-shot), which is fine.
_WORKER_CLIENT = None
_WORKER_CLIENT_LOCK = threading.Lock()


def _worker_client():
    global _WORKER_CLIENT
    if _WORKER_CLIENT is None:
        with _WORKER_CLIENT_LOCK:  # scheduler thread + uvicorn may race the first call
            if _WORKER_CLIENT is None:
                import httpx

                # default covers the long-running LLM judge; callers may override per request
                _WORKER_CLIENT = httpx.Client(timeout=600.0)
    return _WORKER_CLIENT


def _judge_via_worker(url, measurements, meta, prior_measurements) -> DependencyDecision:
    """POST /judge to the agent worker. Any failure -> LLMUnavailable (explicit degradation)."""
    import httpx

    body = {
        "measurements": [m.model_dump() for m in measurements],
        "meta": meta,
        "prior_measurements": [m.model_dump() for m in (prior_measurements or [])],
    }
    try:
        resp = _worker_client().post(f"{url.rstrip('/')}/judge", json=body)
    except httpx.HTTPError as e:
        raise LLMUnavailable(f"agent worker unreachable: {type(e).__name__}: {e}") from e
    if resp.status_code != 200:
        raise LLMUnavailable(f"agent worker error {resp.status_code}: {resp.text[:200]}")
    try:
        return DependencyDecision.model_validate(resp.json())
    except ValueError as e:
        raise LLMUnavailable(f"agent worker returned invalid decision: {e}") from e


def judge_dependencies(
    measurements: list[DependencyMeasurement],
    meta: dict,
    prior_measurements: list[DependencyMeasurement] | None = None,
    agent: Agent | None = None,
) -> DependencyDecision:
    # --- agent-worker mode: the judge lives behind an HTTP boundary (enabled by agent.worker_url) ---
    # An explicitly passed agent (tests, local runs) takes precedence over worker_url.
    if agent is None:
        from norn_core.config import get_settings

        worker_url = get_settings().agent.worker_url
        if worker_url:
            return _judge_via_worker(worker_url, measurements, meta, prior_measurements)
    # --- build the model/agent and call it — all under try ---
    # We narrowly catch only known infrastructure failures (missing credential,
    # invalid config, model/transport error) and re-raise them as a typed
    # LLMUnavailable. The boundary (analyze_dependencies) catches it, logs the
    # traceback and degrades explicitly. Programming bugs are NOT masked — they
    # propagate as-is.
    from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior, UserError

    try:
        agent = agent or build_agent()
        # --- build the prompt: header with segments/metric + the methods' current evidence ---
        prompt = (
            f"Segments: source={meta['source_segment']} target={meta['target_segment']} "
            f"metric={meta['metric_name']}.\nCurrent evidence:\n"
            + json.dumps([m.model_dump() for m in measurements], indent=2)
        )
        # --- add the previous run's evidence to assess dependency drift ---
        if prior_measurements:
            prompt += "\nPrior evidence (previous run):\n" + json.dumps(
                [m.model_dump() for m in prior_measurements], indent=2
            )
        # --- synchronous agent call -> structured decision ---
        return agent.run_sync(prompt).output
    except (KeyError, ValueError, UnexpectedModelBehavior, ModelHTTPError, UserError,
            ConnectionError, OSError) as e:
        raise LLMUnavailable(f"{type(e).__name__}: {e}") from e
