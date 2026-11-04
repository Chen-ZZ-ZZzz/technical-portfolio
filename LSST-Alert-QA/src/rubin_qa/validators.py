"""Completeness validation — checks all required data is present and usable."""

import pandas as pd

from .config import DEFAULT_SURVEY

# Magnitudes: how far an ANTARES locus's brightest detection may sit from the server's own
# brightest_alert_magnitude before the two count as different. When the server counts the
# same alerts, both are one alert's ant_mag, so this only absorbs float rounding; a real
# difference means the server counted a different set of alerts.
BRIGHTEST_TOLERANCE_MAG = 1e-4


def validate_completeness(data: dict, survey: str = DEFAULT_SURVEY) -> list:
    """
    Check that all required data is present and usable.
    Returns a list of issue tokens (empty = fully complete).

    Checks (ZTF):
      no_detections       — dets is empty
      no_magstats         — ms is empty (ZTF only — see below)
      ndet_lt_2           — fewer than 2 detections (unconfirmed)
      coordinates_missing — ra/dec null or absent in dets
      mag_null            — no usable magnitude (magpsf for ZTF, psfFlux for LSST)
      rb_absent           — ZTF: rb score missing; LSST: reliability score missing
      drb_absent          — ZTF only: deep real/bogus score missing
      no_classification   — probabilities are empty (this token's one owner: the
                            classifier returns no flag for it)
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
        if ms.empty and survey != "lsst":
            issues.append("no_magstats")
        if probs.empty:
            issues.append("no_classification")
        return issues

    ndet = ms["ndet"].sum() if not ms.empty else len(dets)
    if ndet < 2:
        issues.append("ndet_lt_2")

    # LSST magstats do not exist to be missing: the multisurvey API raises
    # NotImplementedError for every object, so this token fired on 100% of LSST
    # rows. Any issue token makes build_qa_row return FLAG, so it forced every
    # LSST object to FLAG and hid its real classification verdict. A condition
    # that is always true of a survey describes the survey, not the object.
    # Drop the "not implemented" case here when ALeRCE ships query_magstats.
    if ms.empty and survey != "lsst":
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
      mag_null            — no magnitude in the detections
      brightest_mismatch  — the detections' brightest magnitude is not the server's
                            brightest_alert_magnitude: it counted a different set of alerts
      no_classification   — no tags (this token's one owner: the classifier returns
                            no flag for it)
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
    elif "brightest_alert_magnitude" in ms.columns:
        served, derived = ms["brightest_alert_magnitude"].iloc[0], ms["magmin"].iloc[0]
        if pd.notna(served) and abs(float(served) - float(derived)) > BRIGHTEST_TOLERANCE_MAG:
            issues.append("brightest_mismatch")

    if not tags:
        issues.append("no_classification")

    return issues
