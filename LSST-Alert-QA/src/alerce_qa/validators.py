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
        if "drb" not in dets.columns or dets["drb"].isna().all():
            issues.append("drb_absent")

    if probs.empty:
        issues.append("no_classification")

    return issues
