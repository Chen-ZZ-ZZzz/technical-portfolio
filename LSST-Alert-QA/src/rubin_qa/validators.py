"""Completeness validation — checks all required data is present and usable."""

import pandas as pd

from .config import DEFAULT_SURVEY


def validate_completeness(data: dict, survey: str = DEFAULT_SURVEY) -> list:
    """
    Check that all required data is present and usable.
    Returns a list of issue tokens (empty = fully complete).

    Checks (ZTF):
      no_detections       — dets is empty
      no_magstats         — ms is empty (always fires for LSST; expected)
      ndet_lt_2           — fewer than 2 detections (unconfirmed)
      coordinates_missing — ra/dec null or absent in dets
      mag_null            — no usable magnitude (magpsf for ZTF, psfFlux for LSST)
      rb_absent           — ZTF: rb score missing; LSST: reliability score missing
      drb_absent          — ZTF only: deep real/bogus score missing
      no_classification   — probabilities are empty
      fetch_error_*       — upstream API failure per field
    """
    issues = []
    dets  = data["dets"]
    ms    = data["ms"]
    probs = data["probs"]

    for e in data["fetch_errors"]:
        field = e.split(":")[0]
        issues.append(f"fetch_error_{field}")

    if dets.empty:
        issues.append("no_detections")
        if ms.empty:
            issues.append("no_magstats")
        if probs.empty:
            issues.append("no_classification")
        return issues

    ndet = ms["ndet"].sum() if not ms.empty else len(dets)
    if ndet < 2:
        issues.append("ndet_lt_2")

    if ms.empty:
        issues.append("no_magstats")

    for col in ("ra", "dec"):
        if col not in dets.columns or dets[col].isna().all():
            issues.append("coordinates_missing")
            break

    if survey == "lsst":
        flux = dets["psfFlux"] if "psfFlux" in dets.columns else None
        if flux is None or (flux <= 0).all() or flux.isna().all():
            issues.append("mag_null")
        if "reliability" not in dets.columns or dets["reliability"].isna().all():
            issues.append("rb_absent")
        # drb has no LSST equivalent — skip
    else:
        if "magpsf" not in dets.columns or dets["magpsf"].isna().all():
            issues.append("mag_null")
        if "rb" not in dets.columns or dets["rb"].isna().all():
            issues.append("rb_absent")
        # NOTE: currently checks presence only, not value. ANTARES pre-filters alerts to:
        #   rb >= 0.55, fwhm <= 5.0 px (PSF width — above 5 = poor seeing),
        #   elong <= 1.2 (major/minor axis ratio — above 1.2 = cosmic ray / satellite trail).
        # Objects from ANTARES already pass all three. For ALeRCE objects these fields may
        # be present but out of range — a distinct (worse) failure than absence. Worth adding
        # rb_low_score, bad_seeing, elongated issue tokens when this validator is extended.
        if "drb" not in dets.columns or dets["drb"].isna().all():
            issues.append("drb_absent")

    if probs.empty:
        issues.append("no_classification")

    return issues


def validate_antares(data: dict) -> list:
    """
    Check completeness for an ANTARES locus data dict.
    Keys: dets, ms, tags, fetch_errors.

    Checks:
      fetch_error_*       — upstream failure per field
      no_detections       — dets is empty
      ndet_lt_2           — fewer than 2 detections
      coordinates_missing — ra/dec absent in dets
      mag_null            — no magnitude data in locus properties
      no_classification   — no tags
    """
    issues = []
    dets = data["dets"]
    ms   = data["ms"]
    tags = data.get("tags", [])

    for e in data["fetch_errors"]:
        field = e.split(":")[0]
        issues.append(f"fetch_error_{field}")

    if dets.empty:
        issues.append("no_detections")
        if not tags:
            issues.append("no_classification")
        return issues

    ndet = int(ms["ndet"].iloc[0]) if not ms.empty else len(dets)
    if ndet < 2:
        issues.append("ndet_lt_2")

    for col in ("ra", "dec"):
        if col not in dets.columns or dets[col].isna().all():
            issues.append("coordinates_missing")
            break

    if ms.empty or ms["magmin"].isna().all():
        issues.append("mag_null")

    if not tags:
        issues.append("no_classification")

    return issues
