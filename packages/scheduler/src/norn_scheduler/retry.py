"""Exponential retries around a single job run (sleep is injected for tests)."""
from __future__ import annotations

import logging
import time
from typing import Callable, TypeVar

T = TypeVar("T")
logger = logging.getLogger(__name__)

# Backoff ceiling: base * 2**attempt is unbounded, and a misconfigured large
# `retries` would otherwise sleep for years on the late attempts.
_MAX_DELAY_SECONDS = 3600.0


def with_retries(
    fn: Callable[[], T], attempts: int, base_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
    no_retry: tuple[type[BaseException], ...] = (),
) -> T:
    """Run fn; on exception retry up to `attempts` times with base*2**i backoff
    (capped at _MAX_DELAY_SECONDS), then re-raise. Exceptions in `no_retry`
    (e.g. configuration errors) re-raise immediately — they do not fix
    themselves between attempts."""
    if attempts < 0:
        raise ValueError(f"attempts must be >= 0, got {attempts}")
    for attempt in range(attempts + 1):
        try:
            return fn()
        except Exception as e:
            if isinstance(e, no_retry) or attempt == attempts:
                raise
            delay = min(base_seconds * (2 ** attempt), _MAX_DELAY_SECONDS)
            logger.warning("attempt %d/%d failed; retrying in %.0fs",
                           attempt + 1, attempts + 1, delay, exc_info=True)
            sleep(delay)
    raise AssertionError("unreachable")
