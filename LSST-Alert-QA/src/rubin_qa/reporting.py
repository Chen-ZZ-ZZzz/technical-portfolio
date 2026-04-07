"""QA row assembly and full pipeline orchestration."""

import math
import time

import pandas as pd

from .classifier import classify_antares, classify_object
from .client import fetch_candidates, fetch_object_data
from .config import (
    DEFAULT_PAGE_SIZE,
    DEFAULT_SURVEY,
    INTER_OBJECT_DELAY,
    OUTPUT_COLUMNS,
)
from .validators import validate_antares, validate_completeness


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
) -> pd.DataFrame:
    """
    Full pipeline: fetch → validate → classify → QA report.

    oids:   optional explicit list of OID strings — skips fetch_candidates.
    survey: "ztf" (default) or "lsst".
      For LSST, magstats are not available via the API — ndet and mag stats
      are computed from raw detections instead.

    Returns a DataFrame (one row per object).
    """
    if oids is None:
        oids = fetch_candidates(page_size=page_size, survey=survey)
    if not oids:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    rows = []
    for i, oid in enumerate(oids, 1):
        print(f"[{i:>3}/{len(oids)}] {oid}", end="  ", flush=True)
        data   = fetch_object_data(oid, survey=survey)
        issues = validate_completeness(data, survey=survey)
        ndet   = int(data["ms"]["ndet"].sum()) if not data["ms"].empty else len(data["dets"])
        cl     = classify_object(data["probs"], ndet)
        row    = build_qa_row(oid, data, issues, cl)
        rows.append(row)
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
) -> pd.DataFrame:
    """
    ANTARES pipeline: fetch loci → validate → classify tags → QA report.

    locus_ids: optional explicit list of ANTARES locus IDs — skips fetch_antares_candidates.
    Returns a DataFrame (one row per locus) with the same schema as run_pipeline.
    """
    from .antares_client import fetch_antares_candidates, fetch_antares_locus

    if locus_ids is None:
        locus_ids = fetch_antares_candidates(page_size=page_size)
    if not locus_ids:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    rows = []
    for i, lid in enumerate(locus_ids, 1):
        print(f"[{i:>3}/{len(locus_ids)}] {lid}", end="  ", flush=True)
        data   = fetch_antares_locus(lid)
        issues = validate_antares(data)
        cl     = classify_antares(data["tags"])
        row    = build_antares_qa_row(lid, data, issues, cl)
        rows.append(row)
        print(row["status"])
        if inter_object_delay > 0:
            time.sleep(inter_object_delay)

    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
