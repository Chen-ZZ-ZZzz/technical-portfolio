"""ANTARES alert broker client — fetch loci and per-locus data."""

import sys

import pandas as pd

from . import retry_budget
from .config import (
    DEFAULT_PAGE_SIZE,
    ERROR_PREFIX,
)

# HTTP statuses that are an answer, not a fault: the request itself is malformed, so
# resending it would only burn the run's retry budget on a guaranteed failure. The same
# codes ALeRCE's side answers (client._api_call); a 404 needs no entry here, because
# antares-client already answers a missing locus with None.
ANSWERED_STATUSES = (400, 422)

# ANTARES's ant_survey codes on an alert, by the survey that took it (the codes: CLAUDE.md,
# "Known API quirks"; 2 is a ZTF upper limit, not LSST). A code missing here is unknown.
ANT_SURVEY = {1: "ZTF", 2: "ZTF", 4: "LSST"}


# ANTARES helpers shared by everything in the repo that still reads ANTARES: this module's
# pipeline, tools/asteroid_probe.py and tools/host_match_check.py. One definition each
# (2026-10-03): two copies drift apart, and a fix made in one quietly misses the other.
# The frozen SSO monitor keeps its own self-built code.


def antares_errors() -> tuple[type[BaseException], ...]:
    """
    What an ANTARES call can raise that is a broker fault worth retrying, not a bug:
      httpx.TransportError  timeouts and dropped connections (antares-client 2.x talks
                            through httpx, with a 60 s timeout of its own)
      AntaresException      an HTTP error answer with a JSON body: a 5xx, or a 4xx that
                            is neither answered by the client (404) nor ANSWERED_STATUSES
      json.JSONDecodeError  the same answer with a body that is not JSON, which the client
                            fails to parse (the API's 500s come as text/plain: README,
                            "Known API Quirks")
    Never pydantic's ValidationError: a response that does not fit the client's schema is
    deterministic, so it must fail at once and loudly. Imported lazily: antares-client,
    and httpx with it, is optional for ALeRCE-only installs.
    """
    import json

    import httpx
    from antares_client.exceptions import AntaresException

    return (httpx.TransportError, AntaresException, json.JSONDecodeError)


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
    """
    ANTARES's own cone filter, built by hand (elasticsearch-dsl cannot), in the form
    antares-client 2.x's cone_search sends: radius in degrees, centre in hmsdms. The
    client's cone_search itself refuses radii above 1°, which is why the tools build the
    clause instead of calling it.
    """
    from astropy.coordinates import SkyCoord

    ra, dec, radius = cone
    location = SkyCoord(ra, dec, unit="deg").to_string("hmsdms")
    return {"sky_distance": {"radius": f"{radius} degree", "location": location}}


def _raise_answered_status(response) -> None:
    """
    httpx response hook on antares-client's HTTP client: a status in ANSWERED_STATUSES
    raises httpx.HTTPStatusError, which carries the status. antares-client turns every
    error answer into AntaresException(body), and the v2 API's error body ({"detail": …})
    holds no status code, so without this hook a malformed request could not be told
    apart from a server fault and would be retried.
    """
    if response.status_code in ANSWERED_STATUSES:
        response.read()
        response.raise_for_status()


def _search():
    """
    The antares_client.search module, with _raise_answered_status installed (once) on
    the HTTP client it talks through. The import is deferred: ALeRCE-only installs need
    no antares-client.
    """
    try:
        from antares_client import search
    except ImportError:
        raise ImportError("antares-client not installed. Run: pip install antares-client")
    hooks = search.api_client.client.event_hooks["response"]
    if _raise_answered_status not in hooks:
        hooks.append(_raise_answered_status)
    return search


def _answered(e: BaseException) -> str | None:
    """The answer label for an ANSWERED_STATUSES reply (see _raise_answered_status)."""
    response = e.response
    if response.status_code not in ANSWERED_STATUSES:
        return None
    return f"bad_request: HTTP {response.status_code}: {response.text[:200]}"


def _api_call(fn, *args, **kwargs):
    """
    Call an ANTARES API function with retry (the loop: retry_budget.call).
    Returns (result, error_str). On failure: result=None, error_str set.

    ANTARES's policy, the same rule as ALeRCE's side: a malformed request
    (ANSWERED_STATUSES, raised as httpx.HTTPStatusError by _raise_answered_status) is an
    answer, "bad_request: …", returned at once. A missing locus is an answer too:
    antares-client returns None for a 404, which the caller reports as
    locus:not_found. What antares_errors() lists is a fault worth retrying; anything
    else, such as a response that fails the client's schema, stops at once.
    """
    import httpx

    return retry_budget.call(
        fn, args, kwargs,
        transient=antares_errors(),
        answers=((httpx.HTTPStatusError, _answered),),
        broker="ANTARES",
    )


def fetch_antares_candidates(page_size: int = DEFAULT_PAGE_SIZE) -> list:
    """
    Fetch the IDs of the most recently updated ANTARES loci (get_random_locus_ids, which
    antares-client 2.x sorts on newest_alert_observation_time; the rule: CLAUDE.md, "QA
    pipeline"). Returns a list of locus_id strings. Empty list on error.

    Deduplicated here; why the list comes back short: README, Known API Quirks.
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


def newest_detections(dets: pd.DataFrame) -> tuple[dict[str, float], int]:
    """
    The newest detection's MJD per survey ({"ZTF": mjd, "LSST": mjd}; a survey with no
    detection is absent), and how many detections carry an ant_survey code ANT_SURVEY
    does not know, or none: counted, never dropped. dets as fetch_antares_locus keeps
    them, the detections magmin and magmax come from (upper limits already gone), so an
    upper limit never counts as a detection here either.
    """
    if dets.empty or "mjd" not in dets.columns:
        return {}, 0
    codes = dets["ant_survey"] if "ant_survey" in dets.columns else pd.Series(None, index=dets.index)
    surveys = codes.map(lambda c: ANT_SURVEY.get(int(c)) if pd.notna(c) else None)
    newest = {s: float(dets.loc[surveys == s, "mjd"].max()) for s in sorted(set(surveys.dropna()))}
    return newest, int(surveys.isna().sum())


def fetch_antares_locus(locus_id: str) -> dict:
    """
    Fetch data for one ANTARES locus.
    Returns dict with keys: dets, ms, tags, fetch_errors.

      dets: DataFrame of alerts — columns include mjd, ra, dec plus alert properties.
      ms:   1-row DataFrame: ndet = num_mag_values from locus.properties; magmin and
            magmax = the brightest and faintest ant_mag of the detections in dets (the
            API serves no faintest_alert_magnitude: README, Known API Quirks);
            brightest_alert_magnitude = the server's own value, as served, which
            validate_antares checks against magmin; newest_alert_observation_time =
            the MJD of the locus's latest alert, and newest_survey = the survey that
            took it (ANT_SURVEY, None if unknown), both reported per run by
            run_antares_pipeline (how many exposures the sample spans, from which survey);
            newest_ztf_mjd, newest_lsst_mjd and unknown_survey_dets from
            newest_detections, over the same detections as magmin and magmax.
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
        # A missing locus: antares-client returns None for the API's 404 (ANT ids) and
        # for a ZTF id no locus carries.
        return {"dets": empty, "ms": empty, "tags": [], "fetch_errors": ["locus:not_found"]}

    # Alerts → dets DataFrame, and the survey of the newest alert (upper limits included)
    dets = empty
    newest_survey = None
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
            if "ant_survey" in dets.columns:
                code = dets.loc[dets["mjd"].idxmax(), "ant_survey"]
                newest_survey = ANT_SURVEY.get(int(code)) if pd.notna(code) else None
            if "ant_mag" in dets.columns:
                dets = dets[dets["ant_mag"].notna()].reset_index(drop=True)
    except Exception as e:
        fetch_errors.append(f"alerts:{e}")

    # ms-like summary row: the count from the locus properties, both magnitude ends from
    # the detections (one computation), and the server's own brightest beside them for
    # validate_antares to check against magmin.
    ms = empty
    try:
        props = locus.properties or {}
        # num_mag_values is ANTARES's quality-filtered count (conservative).
        # len(dets) after ant_mag.notna() filter may be slightly higher (e.g. 59 vs 56)
        # because ANTARES applies additional cuts not reflected in the raw alert stream.
        # Prefer num_mag_values as the authoritative ndet for validators.
        n_mag = props.get("num_mag_values") or (len(dets) if not dets.empty else 0)
        mags = dets["ant_mag"] if "ant_mag" in dets.columns else pd.Series(dtype=float)
        newest, unknown_dets = newest_detections(dets)
        ms = pd.DataFrame([{
            "ndet":   n_mag,
            "magmin": mags.min() if not mags.empty else None,
            "magmax": mags.max() if not mags.empty else None,
            "brightest_alert_magnitude": props.get("brightest_alert_magnitude"),
            "newest_alert_observation_time": props.get("newest_alert_observation_time"),
            "newest_survey": newest_survey,
            "newest_ztf_mjd": newest.get("ZTF"),
            "newest_lsst_mjd": newest.get("LSST"),
            "unknown_survey_dets": unknown_dets,
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
