"""Экспоненциальные ретраи вокруг одного запуска джобы (sleep инъектируется для тестов)."""
from __future__ import annotations

import logging
import time
from typing import Callable, TypeVar

T = TypeVar("T")
logger = logging.getLogger(__name__)


def with_retries(
    fn: Callable[[], T], attempts: int, base_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Run fn; on exception retry up to `attempts` times with base*2**i backoff, then re-raise."""
    for attempt in range(attempts + 1):
        try:
            return fn()
        except Exception:
            if attempt == attempts:
                raise
            delay = base_seconds * (2 ** attempt)
            logger.warning("attempt %d/%d failed; retrying in %.0fs",
                           attempt + 1, attempts + 1, delay, exc_info=True)
            sleep(delay)
    raise AssertionError("unreachable")
