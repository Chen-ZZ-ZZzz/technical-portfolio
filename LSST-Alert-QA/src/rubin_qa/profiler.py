"""
Object profiler — diagnostic deep-dive for one object, on any supported broker.

Complements the pipeline rather than repeating it: run_pipeline says an object is
FLAG or REVIEW_MAJOR, this says why, in the broker's own terms.

Broker coverage (verified against the live APIs 2026-09-12):
  ztf      ALeRCE legacy — real magstats, query_lightcurve (detections + upper limits)
  lsst     ALeRCE multisurvey — query_lightcurve works and additionally carries
           forced photometry; magstats raise NotImplementedError, so the per-band
           table is derived from the detections instead
  antares  locus.lightcurve — one frame carrying detections, upper limits and the
           corrected magnitudes; tag-based classification
"""

import sys

import pandas as pd

from .classifier import classify_antares, classify_object
from .client import _api_call, _client
from .config import DEFAULT_SURVEY, ERROR_PREFIX, SURVEYS
from .reporting import _psfflux_to_mag

# ZTF filter-id → band name. LSST and ANTARES both name their bands in the payload.
FID_MAP = {1: "g", 2: "r", 3: "i"}

_RULE = "=" * 64


def _rule(title: str) -> None:
    print(f"\n{_RULE}\n{title}\n{_RULE}")


def _section(title: str) -> None:
    print(f"\n-- {title} --")


def _per_band(dets: pd.DataFrame, band: str, mag: str, mjd: str = "mjd") -> pd.DataFrame:
    """
    Per-band photometry summary in the shape of a magstats table, computed from
    detections. ALeRCE serves real magstats for ZTF only, and ANTARES serves none
    at all, so the other two brokers get theirs derived here.
    """
    if dets.empty or band not in dets.columns or mag not in dets.columns:
        return pd.DataFrame()

    rows = []
    for name, group in dets.groupby(band):
        mags = group[mag].dropna()
        if mags.empty:
            continue
        row = {
            "band":     name,
            "ndet":     len(group),
            "magmin":   round(float(mags.min()), 3),
            "magmax":   round(float(mags.max()), 3),
            "magmean":  round(float(mags.mean()), 3),
            "magsigma": round(float(mags.std(ddof=0)), 3),
        }
        if mjd in group.columns:
            row["firstmjd"] = round(float(group[mjd].min()), 3)
            row["lastmjd"] = round(float(group[mjd].max()), 3)
        rows.append(row)
    return pd.DataFrame(rows)


def _print_frame(df: pd.DataFrame, empty_note: str) -> None:
    print(df.to_string(index=False) if not df.empty else f"  {empty_note}")


def _print_verdict(cl: dict) -> None:
    """The weighted-consensus verdict, shared by every broker."""
    if cl.get("top_class") is None:
        print(f"  No classification. ({cl.get('flag') or 'no reason given'})")
        return
    prob, consensus = cl.get("class_prob"), cl.get("consensus")
    print(f"\n  Verdict:     {cl['top_class']}"
          + (f"  (best prob {prob:.3f}" if prob is not None else "  (")
          + (f", weighted consensus {consensus:.0%})" if consensus is not None else ")"))
    print(f"  Classifiers: {cl['n_agree']}/{cl['n_classifiers']} "
          f"vote for '{cl['top_class']}'")
    if cl.get("flag"):
        print(f"  FLAG: {cl['flag']}")


def _profile_alerce(oid: str, survey: str) -> None:
    """ZTF and LSST both go through ALeRCE; only the column names differ."""
    is_lsst = survey == "lsst"
    mag_col = "psfFlux" if is_lsst else "magpsf"
    band_col = "band_name" if is_lsst else "fid"

    _section("Classification (all classifiers, ranking=1)")
    probs, err = _api_call(_client.query_probabilities, oid, format="pandas", survey=survey)
    ndet = 0
    if probs is not None and not probs.empty:
        top = (
            probs[probs["ranking"] == 1][["classifier_name", "class_name", "probability"]]
            .sort_values("probability", ascending=False)
        )
        print(top.to_string(index=False))
    else:
        print(f"  No probabilities available.{'' if probs is not None else f' ({err})'}")

    _section("Photometry by band")
    if is_lsst:
        # query_magstats raises NotImplementedError across the whole multisurvey
        # API, so there is nothing to call — derive the table from detections.
        print("  (derived from detections — ALeRCE serves no LSST magstats)")
        dets, _ = _api_call(_client.query_detections, oid, format="pandas", survey=survey)
        dets = dets if dets is not None else pd.DataFrame()
        if not dets.empty and mag_col in dets.columns:
            dets = dets.assign(_mag=_psfflux_to_mag(dets[mag_col]))
        table = _per_band(dets, band_col, "_mag")
        ndet = len(dets)
    else:
        ms, _ = _api_call(_client.query_magstats, oid, format="pandas", survey=survey)
        ms = ms if ms is not None else pd.DataFrame()
        ndet = int(ms["ndet"].sum()) if not ms.empty else 0
        if not ms.empty:
            ms = ms.assign(band=ms["fid"].map(FID_MAP))
        table = ms[[c for c in ("band", "ndet", "magmin", "magmax", "magmean",
                                "magsigma", "firstmjd", "lastmjd") if c in ms.columns]] \
            if not ms.empty else pd.DataFrame()
    _print_frame(table, "No photometry available.")

    if probs is not None and not probs.empty:
        _print_verdict(classify_object(probs, ndet))

    _section("Light curve")
    lc, err = _api_call(_client.query_lightcurve, oid, format="json", survey=survey)
    if err or lc is None:
        print(f"  Light curve unavailable: {err}")
        return

    dets = pd.DataFrame(lc.get("detections", []))
    nondets = pd.DataFrame(lc.get("non_detections", []))
    forced = pd.DataFrame(lc.get("forced_photometry", []))
    print(f"  Detections:     {len(dets)} epochs")
    print(f"  Non-detections: {len(nondets)} upper limits")
    if is_lsst:
        # Rubin publishes forced photometry where ZTF publishes upper limits;
        # non_detections comes back empty on LSST even for well-sampled objects.
        print(f"  Forced phot.:   {len(forced)} epochs")

    if dets.empty:
        return
    if "mjd" in dets.columns:
        print(f"  MJD range:      {dets['mjd'].min():.1f} – {dets['mjd'].max():.1f}")
    if band_col in dets.columns:
        bands = sorted(dets[band_col].dropna().unique().tolist())
        print(f"  Bands seen:     {[FID_MAP.get(b, b) for b in bands] if not is_lsst else bands}")
    if mag_col in dets.columns:
        mags = _psfflux_to_mag(dets[mag_col]) if is_lsst else dets[mag_col].dropna()
        if not mags.empty:
            print(f"  Mag range:      {mags.min():.2f} – {mags.max():.2f}")
    if is_lsst:
        for col, label in (("reliability", "Reliability"), ("snr", "SNR")):
            if col in dets.columns and dets[col].notna().any():
                print(f"  {label + ':':15} "
                      f"{dets[col].min():.3f} – {dets[col].max():.3f}")
    elif "magpsf_corr" in dets.columns:
        print(f"  Corrected:      {dets['magpsf_corr'].notna().sum()} / {len(dets)} "
              f"have magpsf_corr")


def _profile_antares(locus_id: str) -> None:
    """ANTARES keeps detections, upper limits and corrected mags in one frame."""
    from .antares_client import _api_call as _antares_call, _search

    search = _search()
    fetch = (
        search.get_by_ztf_object_id if str(locus_id).upper().startswith("ZTF")
        else search.get_by_id
    )
    locus, err = _antares_call(fetch, locus_id)
    if err or locus is None:
        print(f"  Locus unavailable: {err or 'not found'}")
        return

    tags = list(getattr(locus, "tags", []) or [])
    _section("Classification (ANTARES tags)")
    print(f"  Raw tags ({len(tags)}): {', '.join(tags) if tags else '(none)'}")
    _print_verdict(classify_antares(tags))

    lc = getattr(locus, "lightcurve", None)
    lc = lc if isinstance(lc, pd.DataFrame) else pd.DataFrame()

    _section("Photometry by band")
    print("  (derived from locus.lightcurve — ANTARES serves no magstats)")
    _print_frame(
        _per_band(lc[lc["ant_mag"].notna()] if "ant_mag" in lc.columns else lc,
                  "ant_passband", "ant_mag", mjd="ant_mjd"),
        "No photometry available.",
    )

    _section("Light curve")
    if lc.empty:
        print("  Light curve unavailable.")
        return
    has_mag = lc["ant_mag"].notna() if "ant_mag" in lc.columns else pd.Series(dtype=bool)
    dets = lc[has_mag]
    # An alert with no ant_mag but an ant_maglim is a non-detection: ANTARES
    # carries both in the same frame rather than in a second endpoint.
    limits = lc[~has_mag & lc["ant_maglim"].notna()] if "ant_maglim" in lc.columns else lc.iloc[:0]
    print(f"  Detections:     {len(dets)} epochs")
    print(f"  Non-detections: {len(limits)} upper limits")
    if dets.empty:
        return
    if "ant_mjd" in dets.columns:
        print(f"  MJD range:      {dets['ant_mjd'].min():.1f} – {dets['ant_mjd'].max():.1f}")
    if "ant_passband" in dets.columns:
        print(f"  Bands seen:     {sorted(dets['ant_passband'].dropna().unique().tolist())}")
    print(f"  Mag range:      {dets['ant_mag'].min():.2f} – {dets['ant_mag'].max():.2f}")
    if "ant_mag_corrected" in dets.columns:
        print(f"  Corrected:      {dets['ant_mag_corrected'].notna().sum()} / {len(dets)} "
              f"have ant_mag_corrected")
    if "ant_survey" in lc.columns:
        print(f"  Source surveys: {sorted(lc['ant_survey'].dropna().unique().tolist())}")


def object_profile(oid: str, survey: str = DEFAULT_SURVEY) -> None:
    """
    Print a full diagnostic profile for one object: classification verdict,
    per-band photometry, and a light curve summary.

    Works on every survey the pipeline supports. Raises ValueError on an unknown
    one rather than half-profiling it against the wrong broker.
    """
    if survey not in SURVEYS:
        raise ValueError(f"survey must be one of {list(SURVEYS)}, got {survey!r}")

    _rule(f"FULL PROFILE: {oid}   [{survey}]")
    if survey == "antares":
        _profile_antares(oid)
    else:
        _profile_alerce(oid, survey)


def main() -> None:
    """CLI entry point: python -m rubin_qa.profiler [survey] oid [oid ...]"""
    import argparse

    parser = argparse.ArgumentParser(description="Diagnostic deep-dive for one object")
    parser.add_argument("survey", nargs="?", default=DEFAULT_SURVEY, choices=SURVEYS,
                        metavar="survey",
                        help=f"Broker/survey: {' | '.join(SURVEYS)} (default: {DEFAULT_SURVEY})")
    parser.add_argument("oids", nargs="+", help="One or more object / locus IDs")
    opts = parser.parse_args()

    for oid in opts.oids:
        try:
            object_profile(oid, survey=opts.survey)
        except Exception as e:  # noqa: BLE001
            print(f"{ERROR_PREFIX}{oid}: profile failed — {type(e).__name__}: {e}",
                  file=sys.stderr)


if __name__ == "__main__":
    main()
