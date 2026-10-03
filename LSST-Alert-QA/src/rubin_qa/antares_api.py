"""ANTARES alert broker client — fetch loci and per-locus data."""

import sys
import time

import pandas as pd
import requests

from . import retry_budget
from .config import (
    DEFAULT_PAGE_SIZE,
    ERROR_PREFIX,
    RETRY_ATTEMPTS,
    RETRY_DELAY,
    WARN_PREFIX,
)


# ANTARES helpers shared by everything in the repo that still reads ANTARES: this module's
# pipeline, tools/asteroid_probe.py and tools/host_match_check.py. One definition each
# (2026-10-03): two copies drift apart, and a fix made in one quietly misses the other.
# The retired SSO monitor keeps its own self-built code, a frozen artifact.


def antares_errors() -> tuple[type[BaseException], ...]:
    """
    What an ANTARES call can raise that is a broker fault, not a bug: requests exceptions
    (ANTARES imposes a 60 s read timeout of its own) and AntaresException (non-404 4xx,
    all 5xx). Imported lazily: antares-client is optional for ALeRCE-only installs.
    """
    from antares_client.exceptions import AntaresException

    return (requests.exceptions.RequestException, AntaresException)


def is_solar_system(props: dict) -> bool:
    """ZTF matched this alert (or locus) to a known asteroid. ANTARES stores the string "null" when it did not."""
    return props.get("ztf_ssnamenr") not in (None, "", "null")


def locus_survey_ids(props: dict) -> dict[str, list[str]]:
    """The survey object IDs on an ANTARES locus (a place, not an object: it can hold several)."""
    s = props.get("survey") or {}
    ztf = list((s.get("ztf") or {}).get("id") or [])
    if not ztf and props.get("ztf_object_id"):
        ztf = [props["ztf_object_id"]]
    return {"ztf": ztf, "lsst": [str(x) for x in (s.get("lsst") or {}).get("dia_object_id") or []]}


def sky_distance_clause(cone: tuple[float, float, float]) -> dict:
    """ANTARES's own cone filter, built by hand (elasticsearch-dsl cannot)."""
    ra, dec, radius = cone
    return {"sky_distance": {"distance": f"{radius} degree", "htm16": {"center": f"{ra} {dec}"}}}


def _search():
    try:
        from antares_client import search
        return search
    except ImportError:
        raise ImportError("antares-client not installed. Run: pip install antares-client")


def _api_call(fn, *args, **kwargs):
    """
    Call an ANTARES API function with retry on transient errors.
    Returns (result, error_str). On failure: result=None, error_str set.

    Mirrors client._api_call, with ANTARES's own failure modes:
      - requests exceptions — ANTARES imposes a 60s read timeout of its own, and
        a timed-out candidate fetch is what costs a whole daily run.
      - AntaresException — raised for non-404 4xx and all 5xx.
    Anything else is a bug, not a transient fault: break without retrying.

    Note: an ANT locus ID that does not exist answers 500, not 404 (measured
    2026-08-06), so it is indistinguishable from a server having a bad moment and
    costs the full 63s of backoff before giving up. IDs from get_random_locus_ids
    always exist, so the daily run never pays this; a typo'd ID on the CLI does.
    The ZTF path is better behaved — get_by_ztf_object_id returns None for an
    unknown object, which surfaces immediately as locus:not_found.
    """
    errors = antares_errors()
    last_err = None
    name = getattr(fn, "__name__", str(fn))
    for attempt in range(RETRY_ATTEMPTS):
        try:
            return fn(*args, **kwargs), None
        except errors as e:
            last_err = str(e)
            if attempt < RETRY_ATTEMPTS - 1:
                granted = retry_budget.consume(RETRY_DELAY * (2 ** attempt))
                if granted is None:
                    # Budget spent: this is a bad run, not a bad moment. Give up
                    # on backoff so the rest of the page fails fast instead of
                    # sleeping its way through the outage.
                    print(
                        f"{WARN_PREFIX}{name} failed ({last_err}) — "
                        f"retry budget exhausted, not retrying",
                        file=sys.stderr, flush=True,
                    )
                    break
                print(
                    f"{WARN_PREFIX}{name} failed ({last_err}) — "
                    f"retry {attempt + 1}/{RETRY_ATTEMPTS - 1} in {granted:.0f}s "
                    f"({retry_budget.remaining():.0f}s budget left)",
                    file=sys.stderr, flush=True,
                )
                time.sleep(granted)
        except Exception as e:
            last_err = str(e)
            break
    return None, last_err


def fetch_antares_candidates(page_size: int = DEFAULT_PAGE_SIZE) -> list:
    """
    Fetch random locus IDs from ANTARES.
    Returns a list of locus_id strings. Empty list on error.

    Expect fewer IDs back than requested. ANTARES's random_score query carries no
    seed and the client pages through results with a fresh request per page, so
    the ordering reshuffles mid-walk and loci recur across pages. Measured loss
    after dedup is 10-15% at page_size=256.
    """
    ids, err = _api_call(_search().get_random_locus_ids, page_size)
    if err is not None:
        print(
            f"{ERROR_PREFIX}fetch_antares_candidates: {err}",
            file=sys.stderr, flush=True,
        )
        return []

    seen = set()
    deduped = []
    for id_ in ids or []:
        if id_ not in seen:
            seen.add(id_)
            deduped.append(id_)
    return deduped


def fetch_antares_locus(locus_id: str) -> dict:
    """
    Fetch data for one ANTARES locus.
    Returns dict with keys: dets, ms, tags, fetch_errors.

      dets: DataFrame of alerts — columns include mjd, ra, dec plus alert properties.
      ms:   1-row DataFrame with ndet, magmin, magmax from locus.properties.
            magmin = brightest_alert_magnitude (lowest magnitude number = brightest).
            magmax = faintest_alert_magnitude.
            Compatible with build_qa_row's ms expectations (.sum() / .min() / .max()).
      tags: list of tag name strings (ANTARES discrete classification labels).
      fetch_errors: list of error tokens.
    """
    fetch_errors = []
    empty = pd.DataFrame()

    s = _search()
    getter = s.get_by_ztf_object_id if locus_id.startswith("ZTF") else s.get_by_id
    locus, err = _api_call(getter, locus_id)
    if err is not None:
        return {"dets": empty, "ms": empty, "tags": [], "fetch_errors": [f"locus:{err}"]}
    if locus is None:
        # 404 → None. In practice only the ZTF path reaches here; an absent ANT
        # ID answers 500 and exits above as a retried failure.
        return {"dets": empty, "ms": empty, "tags": [], "fetch_errors": ["locus:not_found"]}

    # Alerts → dets DataFrame
    dets = empty
    try:
        alerts = locus.alerts or []
        if alerts:
            records = []
            for a in alerts:
                rec = {"mjd": a.mjd, "ra": locus.ra, "dec": locus.dec}
                if a.properties:
                    rec.update(a.properties)
                records.append(rec)
            dets = pd.DataFrame(records)
            if "ant_mag" in dets.columns:
                dets = dets[dets["ant_mag"].notna()].reset_index(drop=True)
    except Exception as e:
        fetch_errors.append(f"alerts:{e}")

    # Locus properties → ms-like summary row
    ms = empty
    try:
        props = locus.properties or {}
        # num_mag_values is ANTARES's quality-filtered count (conservative).
        # len(dets) after ant_mag.notna() filter may be slightly higher (e.g. 59 vs 56)
        # because ANTARES applies additional cuts not reflected in the raw alert stream.
        # Prefer num_mag_values as the authoritative ndet for validators.
        n_mag = props.get("num_mag_values") or (len(dets) if not dets.empty else 0)
        ms = pd.DataFrame([{
            "ndet":   n_mag,
            "magmin": props.get("brightest_alert_magnitude"),
            "magmax": props.get("faintest_alert_magnitude"),
        }])
    except Exception as e:
        fetch_errors.append(f"properties:{e}")

    # Tags
    tags = []
    try:
        tags = list(locus.tags) if locus.tags else []
    except Exception as e:
        fetch_errors.append(f"tags:{e}")

    return {"dets": dets, "ms": ms, "tags": tags, "fetch_errors": fetch_errors}
