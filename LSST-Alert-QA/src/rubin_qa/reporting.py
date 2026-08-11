"""QA row assembly and full pipeline orchestration."""

import math
import sys
import time

import pandas as pd

from . import retry_budget
from .classifier import classify_antares, classify_object
from .client import fetch_candidates, fetch_object_data
from .config import (
    DEADLINE_FLOOR,
    DEADLINE_SLACK,
    DEFAULT_PAGE_SIZE,
    DEFAULT_SECONDS_PER_OBJECT,
    DEFAULT_SURVEY,
    INTER_OBJECT_DELAY,
    OUTPUT_COLUMNS,
    SECONDS_PER_OBJECT,
    WARN_PREFIX,
)
from .validators import validate_antares, validate_completeness


def estimate_runtime(n: int, survey: str = DEFAULT_SURVEY) -> float:
    """Estimated wall-clock seconds for an n-object run, from measured per-object cost."""
    return n * SECONDS_PER_OBJECT.get(survey, DEFAULT_SECONDS_PER_OBJECT)


def deadline_for(n: int, survey: str = DEFAULT_SURVEY) -> float:
    """
    Deadline sized to the job rather than fixed: DEADLINE_SLACK × its own estimate,
    never below DEADLINE_FLOOR. A 5-hour scan is allowed to run 5 hours; what trips
    is a run dragging far past what its own size predicts.
    """
    return max(DEADLINE_FLOOR, DEADLINE_SLACK * estimate_runtime(n, survey))


def _psfflux_to_mag(flux_series: pd.Series) -> pd.Series:
    """Convert LSST psfFlux (nanojanskies) to AB magnitudes. Drops non-positive values."""
    positive = flux_series[flux_series > 0].dropna()
    return positive.apply(lambda f: -2.5 * math.log10(f) + 31.4)


def build_qa_row(oid: str, data: dict, issues: list, cl: dict) -> dict:
    """
    Assemble one output row from pre-computed inputs. No API calls.
    status: PASS (no flag) / REVIEW (genuine split) / FLAG (everything else)
    """
    dets = data["dets"]
    ms   = data["ms"]

    # Detection counts and magnitude range
    if not ms.empty:
        ndet   = int(ms["ndet"].sum())
        magmin = float(ms["magmin"].min())
        magmax = float(ms["magmax"].max())
    elif not dets.empty:
        ndet = len(dets)
        if "magpsf" in dets.columns and not dets["magpsf"].isna().all():
            mags = dets["magpsf"].dropna()
        elif "psfFlux" in dets.columns:
            mags = _psfflux_to_mag(dets["psfFlux"])
        else:
            mags = pd.Series(dtype=float)
        magmin = float(mags.min()) if not mags.empty else float("nan")
        magmax = float(mags.max()) if not mags.empty else float("nan")
    else:
        ndet = 0
        magmin = magmax = float("nan")

    mag_range = round(magmax - magmin, 4) if magmin == magmin and magmax == magmax else float("nan")

    # Timespan: last − first detection epoch
    timespan_days = float("nan")
    if not dets.empty:
        epoch_col = next((c for c in ("mjd", "jd") if c in dets.columns), None)
        if epoch_col:
            span = dets[epoch_col].max() - dets[epoch_col].min()
            timespan_days = round(float(span), 2)

    confirmed = ndet > 1

    # Merge completeness issues and classification flag into one flag string
    all_flags = []
    if issues:
        all_flags.append("completeness: " + ", ".join(issues))
    if cl.get("n_classifiers", 0) < 2:
        all_flags.append("insufficient_classifiers")
    if cl.get("flag"):
        all_flags.append(cl["flag"])
    flag = "; ".join(all_flags) or None

    if issues:
        status = "FLAG"
    elif cl.get("n_classifiers", 0) < 2:
        status = "FLAG"
    elif cl.get("verdict") == "pass":
        status = "PASS"
    elif cl.get("verdict") == "review_minor":
        status = "REVIEW_MINOR"
    else:
        status = "REVIEW_MAJOR"

    return {
        "oid":                oid,
        "ndet":               ndet,
        "mag_range":          mag_range,
        "timespan_days":      timespan_days,
        "top_class":          cl.get("top_class"),
        "class_prob":         round(cl["class_prob"], 4) if cl.get("class_prob") is not None else None,
        "consensus":          round(cl["consensus"], 4)  if cl.get("consensus")  is not None else None,
        "n_classifiers":      cl.get("n_classifiers", 0),
        "n_agree":            cl.get("n_agree", 0),
        "n_disagree":         cl.get("n_disagree", 0),
        "confirmed":          confirmed,
        "has_issues":         bool(issues),
        "completeness_issues": issues,
        "flag":               flag,
        "status":             status,
    }


def run_pipeline(
    page_size: int = DEFAULT_PAGE_SIZE,
    survey: str = DEFAULT_SURVEY,
    oids: list | None = None,
    inter_object_delay: float = INTER_OBJECT_DELAY,
    quiet: bool = False,
    max_run_seconds: float | None = None,
) -> pd.DataFrame:
    """
    Full pipeline: fetch → validate → classify → QA report.

    oids:   optional explicit list of OID strings — skips fetch_candidates.
    survey: "ztf" (default) or "lsst".
      For LSST, magstats are not available via the API — ndet and mag stats
      are computed from raw detections instead.
    quiet:  suppress per-object progress output.
    max_run_seconds: wall-clock ceiling on the per-object loop. None (default)
      sizes it from the job via deadline_for(); 0 disables it entirely. On expiry
      the run stops and returns the rows gathered so far, so a stalled broker
      costs a short report rather than an open-ended job. Retry backoff is capped
      separately by the run-wide retry budget.

    Returns a DataFrame (one row per object).
    """
    retry_budget.reset()

    if oids is None:
        oids = fetch_candidates(page_size=page_size, survey=survey)
    if not oids:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    # Sized from the resolved object count, not page_size — the two differ once
    # duplicates are dropped.
    if max_run_seconds is None:
        max_run_seconds = deadline_for(len(oids), survey)

    started = time.monotonic()
    rows = []
    for i, oid in enumerate(oids, 1):
        elapsed = time.monotonic() - started
        if max_run_seconds and elapsed > max_run_seconds:
            print(
                f"{WARN_PREFIX}run_pipeline: stopped after {i - 1}/{len(oids)} objects "
                f"— {elapsed:.0f}s elapsed, past the {max_run_seconds:.0f}s deadline "
                f"({DEADLINE_SLACK:g}× this job's estimate). Upstream or network is "
                f"stalled; returning partial report",
                file=sys.stderr, flush=True,
            )
            break
        if not quiet:
            print(f"[{i:>3}/{len(oids)}] {oid}", end="  ", flush=True)
        data   = fetch_object_data(oid, survey=survey)
        issues = validate_completeness(data, survey=survey)
        ndet   = int(data["ms"]["ndet"].sum()) if not data["ms"].empty else len(data["dets"])
        cl     = classify_object(data["probs"], ndet)
        row    = build_qa_row(oid, data, issues, cl)
        rows.append(row)
        if not quiet:
            print(row["status"])
        if inter_object_delay > 0:
            time.sleep(inter_object_delay)

    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)


def build_antares_qa_row(locus_id: str, data: dict, issues: list, cl: dict) -> dict:
    """
    Assemble one QA row for an ANTARES locus. Same output schema as build_qa_row.

    No n_classifiers<2 threshold: ANTARES provides discrete tags, not multi-classifier
    probability votes — tag presence is the classification signal.
    """
    dets = data["dets"]
    ms   = data["ms"]

    if not ms.empty:
        ndet   = int(ms["ndet"].iloc[0])
        magmin = float(ms["magmin"].iloc[0]) if ms["magmin"].notna().any() else float("nan")
        magmax = float(ms["magmax"].iloc[0]) if ms["magmax"].notna().any() else float("nan")
    elif not dets.empty:
        ndet = len(dets)
        magmin = magmax = float("nan")
    else:
        ndet = 0
        magmin = magmax = float("nan")

    mag_range = round(magmax - magmin, 4) if magmin == magmin and magmax == magmax else float("nan")

    timespan_days = float("nan")
    if not dets.empty and "mjd" in dets.columns:
        span = dets["mjd"].max() - dets["mjd"].min()
        timespan_days = round(float(span), 2)

    confirmed = ndet > 1

    all_flags = []
    if issues:
        all_flags.append("completeness: " + ", ".join(issues))
    if cl.get("flag"):
        all_flags.append(cl["flag"])
    flag = "; ".join(all_flags) or None

    if issues:
        status = "FLAG"
    elif cl.get("verdict") == "pass":
        status = "PASS"
    elif cl.get("verdict") == "review_minor":
        status = "REVIEW_MINOR"
    else:
        status = "REVIEW_MAJOR"

    return {
        "oid":                 locus_id,
        "ndet":                ndet,
        "mag_range":           mag_range,
        "timespan_days":       timespan_days,
        "top_class":           cl.get("top_class"),
        "class_prob":          round(cl["class_prob"], 4) if cl.get("class_prob") is not None else None,
        "consensus":           round(cl["consensus"], 4)  if cl.get("consensus")  is not None else None,
        "n_classifiers":       cl.get("n_classifiers", 0),
        "n_agree":             cl.get("n_agree", 0),
        "n_disagree":          cl.get("n_disagree", 0),
        "confirmed":           confirmed,
        "has_issues":          bool(issues),
        "completeness_issues": issues,
        "flag":                flag,
        "status":              status,
    }


def run_antares_pipeline(
    page_size: int = DEFAULT_PAGE_SIZE,
    locus_ids: list | None = None,
    inter_object_delay: float = INTER_OBJECT_DELAY,
    quiet: bool = False,
    max_run_seconds: float | None = None,
) -> pd.DataFrame:
    """
    ANTARES pipeline: fetch loci → validate → classify tags → QA report.

    locus_ids:       optional explicit list of ANTARES locus IDs — skips fetch_antares_candidates.
    quiet:           suppress per-object progress output.
    max_run_seconds: wall-clock ceiling on the per-locus loop. None (default) sizes
                     it from the job via deadline_for(); 0 disables it entirely. On
                     expiry the run stops and returns the rows gathered so far, so a
                     stalled broker costs a short report rather than an open-ended
                     job. Retry backoff is capped separately by the retry budget.
    Returns a DataFrame (one row per locus) with the same schema as run_pipeline.
    """
    from .antares_client import fetch_antares_candidates, fetch_antares_locus

    retry_budget.reset()

    if locus_ids is None:
        locus_ids = fetch_antares_candidates(page_size=page_size)
    if not locus_ids:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    # Sized from the deduplicated locus count, which is 10-15% below page_size.
    if max_run_seconds is None:
        max_run_seconds = deadline_for(len(locus_ids), "antares")

    started = time.monotonic()
    rows = []
    for i, lid in enumerate(locus_ids, 1):
        elapsed = time.monotonic() - started
        if max_run_seconds and elapsed > max_run_seconds:
            print(
                f"{WARN_PREFIX}run_antares_pipeline: stopped after {i - 1}/{len(locus_ids)} "
                f"loci — {elapsed:.0f}s elapsed, past the {max_run_seconds:.0f}s deadline "
                f"({DEADLINE_SLACK:g}× this job's estimate). Upstream or network is "
                f"stalled; returning partial report",
                file=sys.stderr, flush=True,
            )
            break
        if not quiet:
            print(f"[{i:>3}/{len(locus_ids)}] {lid}", end="  ", flush=True)
        data   = fetch_antares_locus(lid)
        issues = validate_antares(data)
        cl     = classify_antares(data["tags"])
        row    = build_antares_qa_row(lid, data, issues, cl)
        rows.append(row)
        if not quiet:
            print(row["status"])
        if inter_object_delay > 0:
            time.sleep(inter_object_delay)

    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
