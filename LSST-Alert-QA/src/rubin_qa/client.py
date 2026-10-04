"""ALeRCE API client — fetch candidates and per-object data."""

import os
import sys
import pandas as pd
import requests

from . import retry_budget
from .config import (
    CACHE_DIR,
    DEFAULT_SURVEY,
    DEFAULT_PAGE_SIZE,
    ERROR_PREFIX,
    REQUEST_TIMEOUT,
    WARN_PREFIX,
)

# The alerce package imports matplotlib.pyplot at its own module top (alerce/ms_stamps.py),
# so every ALeRCE user loads matplotlib, plotting or not. Its font cache goes under cache/
# (refetchable), set before that import; an MPLCONFIGDIR already set wins. Without it, a
# sandboxed run (home directory closed) rebuilt the font cache on every command.
os.environ.setdefault("MPLCONFIGDIR", str(CACHE_DIR / "matplotlib"))

from alerce.core import Alerce  # noqa: E402
from alerce.exceptions import APIError, ObjectNotFoundError, ParseError  # noqa: E402


def _force_session_timeout(client, timeout: float = REQUEST_TIMEOUT) -> int:
    """
    Make every requests.Session inside `client` apply a default timeout.

    The alerce package never passes `timeout` to session.request, so requests
    blocks forever on a hung connection. The per-run ceilings cannot save us
    there — both are checked between calls and cannot interrupt a blocked read.

    The Alerce object holds one session directly and one per sub-client
    (legacy/multisurvey × search/stamps), so all of them need wrapping. An
    explicit timeout at the call site still wins. Returns how many were patched.
    """
    holders = [client] + [v for v in vars(client).values() if hasattr(v, "__dict__")]
    seen = set()
    patched = 0

    for holder in holders:
        session = getattr(holder, "session", None)
        if not isinstance(session, requests.Session) or id(session) in seen:
            continue
        seen.add(id(session))
        if getattr(session, "_rubin_qa_timeout", False):
            continue  # already wrapped; don't nest wrappers

        def with_timeout(*args, _original=session.request, **kwargs):
            kwargs.setdefault("timeout", timeout)
            return _original(*args, **kwargs)

        session.request = with_timeout
        session._rubin_qa_timeout = True
        patched += 1

    return patched


_client = Alerce()
_force_session_timeout(_client)


def _api_call(fn, *args, **kwargs):
    """
    Call an ALeRCE API function with retry (the loop: retry_budget.call).
    Returns (result, error_str). On failure: result=None, error_str set.

    ALeRCE's policy: APIError is a fault worth retrying. ObjectNotFoundError is an
    answer ("not_found"), and so is ParseError, which alerce raises for HTTP 400:
    the request itself is malformed, and resending it would only burn the run's
    retry budget on a guaranteed failure.
    """
    return retry_budget.call(
        fn, args, kwargs,
        transient=(APIError,),
        answers=((ObjectNotFoundError, lambda e: "not_found"), (ParseError, lambda e: f"bad_request: {e}")),
    )


def fetch_candidates(page_size: int = DEFAULT_PAGE_SIZE, survey: str = DEFAULT_SURVEY) -> list:
    """
    Fetch a page of object IDs from ALeRCE with no class restriction.
    Returns a deduplicated list of oid strings. Empty list on error.
    LSST oids are integers from the API — normalized to str here.
    """
    result, err = _api_call(
        _client.query_objects,
        page_size=page_size,
        survey=survey,
    )
    if err or result is None:
        print(
            f"{ERROR_PREFIX}fetch_candidates: {err or 'no result'}",
            file=sys.stderr, flush=True,
        )
        return []
    if result.empty:
        # Not a failure — the query succeeded and the page held nothing.
        print(
            f"{WARN_PREFIX}fetch_candidates: empty result",
            file=sys.stderr, flush=True,
        )
        return []
    oids = [str(o) for o in result["oid"].tolist()]
    seen = set()
    return [o for o in oids if not (o in seen or seen.add(o))]


def fetch_object_data(oid: str, survey: str = DEFAULT_SURVEY) -> dict:
    """
    Fetch detections, magstats, and probabilities for one object.
    Each call is independent — a failure returns an empty DataFrame
    and is recorded in fetch_errors without aborting the other calls.

    For LSST, query_magstats raises NotImplementedError (not yet in the API).
    ms will be empty and ndet/mag stats fall back to raw detections in build_qa_row.

    Returns dict with keys: dets, ms, probs, fetch_errors.
    """
    empty = pd.DataFrame()
    fetch_errors = []

    dets, err = _api_call(_client.query_detections, oid, format="pandas", survey=survey)
    if err or dets is None:
        dets = empty
        fetch_errors.append(f"detections:{err}")

    ms, err = _api_call(_client.query_magstats, oid, format="pandas", survey=survey)
    if err or ms is None:
        ms = empty
        if err != "Multisurvey query_magstats not implemented.":
            fetch_errors.append(f"magstats:{err}")

    probs, err = _api_call(_client.query_probabilities, oid, format="pandas", survey=survey)
    if err or probs is None:
        probs = empty
        fetch_errors.append(f"probabilities:{err}")

    return {"dets": dets, "ms": ms, "probs": probs, "fetch_errors": fetch_errors}
