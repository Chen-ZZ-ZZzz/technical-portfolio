"""
The retry loop both broker clients use, and the per-run ceiling on time spent
sleeping between retries.

Retry is per-object in both clients — and ALeRCE makes three retried calls per
object — so a broker-wide outage would otherwise multiply backoff across a whole
page. One budget covers one run; each pipeline calls reset() before its loop.

Shared rather than per-client on purpose: the budget answers "how long may this
run stall", which is a property of the run, not of which broker it talks to. The
loop is shared for the same reason, and because two copies drift: each client
passes only its own exception policy (client._api_call, antares_api._api_call).
"""

import sys
import time
from typing import Any, Callable

from .config import RETRY_ATTEMPTS, RETRY_BUDGET_SECONDS, RETRY_DELAY, WARN_PREFIX

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


def call(
    fn: Callable,
    args: tuple,
    kwargs: dict,
    transient: tuple[type[BaseException], ...],
    answers: tuple[tuple[type[BaseException], Callable[[BaseException], str]], ...] = (),
) -> tuple[Any, str | None]:
    """
    One broker call with retry. Returns (result, None), or (None, error string).

      transient  a fault worth retrying: up to RETRY_ATTEMPTS calls with exponential
                 backoff (RETRY_DELAY * 2**attempt), each sleep drawn from this run's
                 budget. Once the budget is spent, the call gives up instead of
                 sleeping through the outage: this is a bad run, not a bad moment.
      answers    (exception, label) pairs that are a real answer, not a fault (an
                 unknown object, a malformed request): returned at once, never retried.
    Anything else is a bug, not a transient fault: stop without retrying.
    """
    last_err = None
    name = getattr(fn, "__name__", str(fn))
    for attempt in range(RETRY_ATTEMPTS):
        try:
            return fn(*args, **kwargs), None
        except BaseException as e:
            label = next((to_label(e) for exc, to_label in answers if isinstance(e, exc)), None)
            if label is not None:
                return None, label
            if not isinstance(e, transient):
                if not isinstance(e, Exception):
                    raise  # KeyboardInterrupt, SystemExit: never swallowed
                last_err = str(e)
                break
            last_err = str(e)
            if attempt < RETRY_ATTEMPTS - 1:
                granted = consume(RETRY_DELAY * (2 ** attempt))
                if granted is None:
                    print(f"{WARN_PREFIX}{name} failed ({last_err}) — retry budget exhausted, not retrying",
                          file=sys.stderr, flush=True)
                    break
                print(f"{WARN_PREFIX}{name} failed ({last_err}) — retry {attempt + 1}/{RETRY_ATTEMPTS - 1} "
                      f"in {granted:.0f}s ({remaining():.0f}s budget left)", file=sys.stderr, flush=True)
                time.sleep(granted)
    return None, last_err
