"""
Object profiler — diagnostic deep-dive for a single ZTF object.
Uses query_lightcurve (not available for LSST yet).
Folded in from explore.py.
"""

import pandas as pd
from alerce.core import Alerce

from .classifier import classify_object
from .client import _api_call

_client = Alerce()

# ZTF filter-id → band name
FID_MAP = {1: "g", 2: "r", 3: "i"}


def object_profile(oid: str, survey: str = "ztf") -> None:
    """
    Print a full diagnostic profile for one object:
      - weighted classification verdict
      - per-filter magstats table
      - light curve summary (detections + non-detections)

    Note: query_lightcurve is ZTF-only. LSST support pending API availability.
    """
    print(f"\n{'='*60}")
    print(f"FULL PROFILE: {oid}")
    print(f"{'='*60}")

    ms, _ = _api_call(_client.query_magstats, oid, format="pandas", survey=survey)
    ndet = int(ms["ndet"].sum()) if ms is not None and not ms.empty else 0

    print("\n-- Classification (all classifiers, ranking=1) --")
    probs, _ = _api_call(_client.query_probabilities, oid, format="pandas", survey=survey)
    if probs is not None and not probs.empty:
        top = (
            probs[probs["ranking"] == 1][["classifier_name", "class_name", "probability"]]
            .sort_values("probability", ascending=False)
        )
        print(top.to_string(index=False))
        cl = classify_object(probs, ndet)
        print(f"\n  Verdict:     {cl['top_class']}  "
              f"(best prob {cl['class_prob']:.3f}, weighted consensus {cl['consensus']:.0%})")
        print(f"  Classifiers: {cl['n_agree']}/{cl['n_classifiers']} vote for '{cl['top_class']}'")
        if cl["flag"]:
            print(f"  FLAG: {cl['flag']}")
    else:
        print("  No probabilities available.")

    if ms is not None and not ms.empty:
        print("\n-- Magstats (per filter) --")
        ms["filter"] = ms["fid"].map(FID_MAP)
        cols = [c for c in ("filter", "ndet", "magmin", "magmax", "magmean", "magsigma",
                            "firstmjd", "lastmjd") if c in ms.columns]
        print(ms[cols].to_string(index=False))

    print("\n-- Light curve summary --")
    lc, err = _api_call(_client.query_lightcurve, oid, format="json", survey=survey)
    if err or lc is None:
        print(f"  Light curve unavailable: {err}")
        return
    dets    = pd.DataFrame(lc["detections"])
    nondets = pd.DataFrame(lc.get("non_detections", []))
    print(f"  Detections:     {len(dets)} epochs")
    print(f"  Non-detections: {len(nondets)} upper limits")
    if not dets.empty and "mjd" in dets.columns:
        print(f"  MJD range:      {dets['mjd'].min():.1f} – {dets['mjd'].max():.1f}")
    if not dets.empty and "fid" in dets.columns:
        print(f"  Filters seen:   {sorted(dets['fid'].unique().tolist())}")
    if not dets.empty and "magpsf" in dets.columns:
        print(f"  Mag range:      {dets['magpsf'].min():.2f} – {dets['magpsf'].max():.2f}")
        print(f"  Corrected:      {dets['magpsf_corr'].notna().sum()} / {len(dets)} have magpsf_corr")
