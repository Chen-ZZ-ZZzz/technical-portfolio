"""
Per-run ceiling on time spent sleeping between retries.

Retry is per-object in both clients — and ALeRCE makes three retried calls per
object — so a broker-wide outage would otherwise multiply backoff across a whole
page. One budget covers one run; each pipeline calls reset() before its loop.

Shared rather than per-client on purpose: the budget answers "how long may this
run stall", which is a property of the run, not of which broker it talks to.
"""

import time

from .config import RETRY_BUDGET_SECONDS

_remaining = RETRY_BUDGET_SECONDS


def reset(seconds: float = RETRY_BUDGET_SECONDS) -> None:
    """Restore the retry sleep budget. Call once at the start of a pipeline run."""
    global _remaining
    _remaining = seconds


def remaining() -> float:
    """Seconds of retry sleep left in the current run."""
    return _remaining


def consume(delay: float) -> float | None:
    """
    Reserve up to `delay` seconds of retry sleep against the run's budget.

    Returns the granted duration (clamped to what is left, so the budget is exact),
    or None if the budget is spent — the caller should then stop retrying and let
    the call fail fast. Does not sleep; the caller does, after logging.
    """
    global _remaining
    if _remaining <= 0:
        return None
    granted = min(delay, _remaining)
    _remaining -= granted
    return granted


def sleep(delay: float) -> float | None:
    """consume() plus the actual sleep. Returns the granted duration, or None."""
    granted = consume(delay)
    if granted is not None:
        time.sleep(granted)
    return granted
