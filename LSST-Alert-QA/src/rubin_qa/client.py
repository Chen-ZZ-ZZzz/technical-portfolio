"""ALeRCE API client — fetch candidates and per-object data."""

import time
import warnings
import pandas as pd
from alerce.core import Alerce
from alerce.exceptions import APIError, ObjectNotFoundError, ParseError

from .config import DEFAULT_SURVEY, DEFAULT_PAGE_SIZE, RETRY_ATTEMPTS, RETRY_DELAY

_client = Alerce()


def _api_call(fn, *args, **kwargs):
    """
    Call an ALeRCE API function with simple retry on transient errors.
    Returns (result, error_str). On failure: result=None, error_str set.
    """
    last_err = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            return fn(*args, **kwargs), None
        except ObjectNotFoundError:
            return None, "not_found"
        except (APIError, ParseError) as e:
            last_err = str(e)
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(RETRY_DELAY * (2 ** attempt))
        except Exception as e:
            last_err = str(e)
            break
    return None, last_err


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
    if err or result is None or result.empty:
        warnings.warn(f"fetch_candidates: {err or 'empty result'}")
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
