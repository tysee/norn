import pytest

from norn_scheduler.retry import with_retries


def test_succeeds_after_failures():
    sleeps: list[float] = []
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("boom")
        return "ok"

    assert with_retries(flaky, attempts=2, base_seconds=30, sleep=sleeps.append) == "ok"
    assert calls["n"] == 3
    assert sleeps == [30, 60]  # exponential: base * 2**attempt


def test_reraises_after_exhaustion():
    def always():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        with_retries(always, attempts=1, base_seconds=1, sleep=lambda _: None)


def test_zero_attempts_runs_once():
    calls = {"n": 0}

    def once():
        calls["n"] += 1
        raise RuntimeError("x")

    with pytest.raises(RuntimeError):
        with_retries(once, attempts=0, base_seconds=1, sleep=lambda _: None)
    assert calls["n"] == 1
