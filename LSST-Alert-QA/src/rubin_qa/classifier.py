"""Weighted classifier consensus — maps a probabilities DataFrame to a QA verdict."""

import re

import pandas as pd

from .config import HIGH_CONFIDENCE_THRESHOLD, MAJORITY_THRESHOLD, OUTLIER_PROB_THRESHOLD

# ANTARES tag taxonomy (from get_available_tags() as of 2026-04).
# NOTE: current tag set reflects ZTF filter implementations only — LSST-equivalent
# science tags are not yet deployed. Revisit SCIENCE_TAGS when LSST tags go live.
#
# SCIENCE_TAGS: astrophysical classifications — count toward consensus.
# All tags are filter outputs, not confirmed classifications. Tags with the _candidate
# suffix are explicitly probabilistic; tags without it are still automated filter passes,
# not spectroscopically verified. Treat every tag as a candidate until followed up.
#
#   dimmers:                    persistently dimming light curve.
#   extragalactic:              positional association (random or real) with extended sources
#                               (2MASS XSC, NED, SDSS, etc.). Coincidence not ruled out —
#                               combine with other tags before drawing conclusions.
#   young_extragalactic_candidate: wide-net filter for young extragalactic transients. Cuts
#                               stars and Galactic-plane alerts but NO catalog crossmatch
#                               (unlike 'extragalactic'). Downstream filtering required.
#   nuclear_transient:          one or more alerts within <0.6 arcsec of a source nucleus in
#                               the ZTF reference frame. Positional criterion only — AGN flare
#                               or TDE candidate, but not classified by this tag alone.
#                               ZTF filter only currently.
#   high_amplitude_variable_star_candidate /
#   high_amplitude_transient_candidate: split by _is_var_star() using Pan-STARRS sgscore1 > 0.4
#                               (closer to 1 = star) plus positional matching. Amplitude =
#                               weighted std dev of light curve (weights = 1/magerr²), threshold
#                               0.5 mag — more rigorous than max-min. Variable star path requires
#                               host-corrected magnitudes; if uncorrected, filter skips entirely.
#                               Transient path always tags regardless of correction status.
#   high_flux_ratio_wrt_nn:     large flux change relative to nearest catalog neighbor within
#                               1 arcsec — i.e. 10^(-0.4*(magpsf - magnr)) >> 1. Flags transient
#                               excess over the quiescent reference source; not a class by itself.
#   recent_reddening:           g-band slope minus r-band slope > 0 within the past 14 days —
#                               color is getting redder recently.
#   imhb_candidate:             galaxy showing possible long-duration, short-timescale variability
#                               characteristic of an accreting intermediate mass black hole (IMBH).
#   soraisam_sublum:            intermediate luminosity optical transient (ILOT) candidate.
#   nova_test:                  transient candidate exhibiting bluer color evolution. Despite the
#                               name, the filter selects on color trend, not nova morphology.
#   sso_candidates:             ZTF detections of previously known Solar System small bodies,
#                               after detection reliability check.
#   sso_confirmed:              subset of sso_candidates where positional residuals vs JPL
#                               Horizons ephemerides are < 1 arcsec.
#   SN_candies:                 SN candidate in the Rubin alert stream.
#   young_rubin_transients:     young transient in the Rubin alert stream.
#   in_m31:                     along the line of sight to M31.
#   blue_transient / ECL_blue_transient_candidate: blue transient candidate (color-selected).
#   dwarf_nova_outburst:        one or more alerts showing dwarf nova outburst activity.
SCIENCE_TAGS: frozenset = frozenset({
    "dimmers",
    "extragalactic",
    "young_extragalactic_candidate",
    "nuclear_transient",
    "high_amplitude_variable_star_candidate",
    "high_amplitude_transient_candidate",
    "blue_transient",
    "ECL_blue_transient_candidate",
    "dwarf_nova_outburst",
    "high_flux_ratio_wrt_nn",
    "recent_reddening",
    "sso_candidates",
    "sso_confirmed",
    "imhb_candidate",
    "SN_candies",
    "soraisam_sublum",
    "young_rubin_transients",
    "nova_test",
    "in_m31",
})

# PIPELINE_TAGS: processing, filter, footprint, or infrastructure tags — excluded from
# classification.
#   high_snr:                   data quality selector — top ~3% by detection confidence
#                               (SNR = 1/sigmapsf). Says "well measured", not "what it is".
#                               Useful in validators: a high_snr object still failing rb or
#                               coordinate checks is more suspicious, since measurement quality
#                               cannot be blamed.
#   lsst_scimma_quality_transient: LSST alert passes quality cuts for transient crossmatching
#                               by SCIMMA: SNR > 10, no quality flags, not a known SSO.
#                               Quality gate, not a classification.
#   iso_forest_anomaly_detection: isolation forest trained on 1M ANTARES loci, 106 LC features.
#                               Aims to tag supernovae, CVs, and unusual events. ML pipeline tag.
#   LAISS_RFC_AD_filter:        random forest classifier/anomaly detector trained on spectroscopic
#                               SNe with LC + host galaxy features. ML pipeline tag.
#   lc_feature_extractor:       computes 78 light curve features for incoming alerts. Pure pipeline.
#   refitt_newsources_snrcut:   new sources with SNR > 5, appropriate for the REFITT broker.
#   siena_mag_coord_cut:        magnitude (r<17) and coordinate filter for Siena College telescope.
#   matheson_extreme_vpdf:      measures how extreme delta_mag is for stars. Variability metric,
#                               not a classification.
#   superphot_plus_classified:  successfully processed by the superphot_plus classifier. Passage
#                               tag only — the classification result is elsewhere.
#   anomaly_transient_dmdt_visited_v1: passed through anomaly_transient_dmdt filter; result
#                               stored in locus property, not this tag.
#   desi_target:                positional match to DESI brightness range — catalog crossmatch,
#                               weak extragalactic hint. Excluded from classification.
#   in_LSSTDDF:                 inside an LSST Deep Drilling Field — spatial/survey footprint only.
#   in_shadow_virgo:            spatial footprint tag.
#   NUTTelA_TAO:                no public description available.
#   random_tagger_filter:       test/kafka infrastructure tag.
#   test_dev_all / test_dev_lsst / test_dev_ztf: development test tags based on siena_mag_coord_cut.
PIPELINE_TAGS: frozenset = frozenset({
    "lc_feature_extractor",
    "refitt_newsources_snrcut",
    "siena_mag_coord_cut",
    "iso_forest_anomaly_detection",
    "LAISS_RFC_AD_filter",
    "desoto_classified",
    "superphot_plus_classified",
    "matheson_extreme_vpdf",
    "in_LSSTDDF",
    "in_shadow_virgo",
    "NUTTelA_TAO",
    "desi_target",
    "lsst_scimma_quality_transient",
    "high_snr",
    "anomaly_transient_dmdt_visited_v1",
    "random_tagger_filter",
    "test_dev_all",
    "test_dev_lsst",
    "test_dev_ztf",
})


def classify_antares(tags: list) -> dict:
    """
    Map ANTARES tags to a QA verdict. Tags are discrete string labels
    (e.g. "nuclear_transient", "supernova") — no per-classifier probabilities.

    Single tag  → consensus=1.0, pass.
    Multi-tag   → consensus=1/n; ambiguity grows with count.
    No tags     → no_classification.

    Return shape matches classify_object so build_antares_qa_row can use it.
    """
    science = [t for t in tags if t in SCIENCE_TAGS]

    if not science:
        pipeline_only = [t for t in tags if t in PIPELINE_TAGS]
        unknown = [t for t in tags if t not in SCIENCE_TAGS and t not in PIPELINE_TAGS]
        flag = (
            f"no_science_tags (pipeline: {pipeline_only}; unknown: {unknown})"
            if (pipeline_only or unknown) else "no_classification"
        )
        return {
            "top_class": None, "class_prob": None, "consensus": None,
            "n_classifiers": 0, "n_agree": 0, "n_disagree": 0,
            "flag": flag, "verdict": "review_major",
        }

    science = sorted(science)
    n = len(science)
    top_class = ", ".join(science)
    consensus = round(1.0 / n, 4)

    if n == 1:
        flag, verdict = None, "pass"
    elif consensus >= MAJORITY_THRESHOLD:
        flag    = f"minor: {n} science tags — {', '.join(science)}"
        verdict = "review_minor"
    else:
        flag    = f"multiple science tags ({n}): {', '.join(science)}"
        verdict = "review_major"

    return {
        "top_class":     top_class,
        "class_prob":    1.0,
        "consensus":     consensus,
        "n_classifiers": n,          # science tags only
        "n_agree":       1,
        "n_disagree":    n - 1,
        "flag":          flag,
        "verdict":       verdict,
    }


def _version_score(version_str) -> float:
    """
    Parse a classifier version string into a small recency multiplier [0.90, 1.10].
    Acts only as a tiebreaker — never overrides method or probability weights.
    """
    if not version_str or not isinstance(version_str, str):
        return 1.0
    nums = re.findall(r"\d+", version_str)
    if not nums:
        return 1.0
    raw = sum(int(n) / (100 ** i) for i, n in enumerate(nums[:3]))
    return 0.90 + min(raw / 50.0, 0.20)


def _method_weight(classifier_name: str, ndet: int) -> float:
    """
    Light curve classifiers outweigh stamp classifiers.
    The gap widens with richer light curve data (more epochs).
      lc:    2.0 → 3.0 as ndet grows to 500+
      stamp: 1.0 → 0.36 as ndet grows (stamp less informative when lc is rich)
    """
    is_lc = "lc_classifier" in classifier_name.lower()
    if is_lc:
        return 2.0 + min(ndet / 500.0, 1.0)
    else:
        return 1.0 / (1.0 + ndet / 500.0)


def classify_object(probs: pd.DataFrame, ndet: int) -> dict:
    """
    Weighted classifier consensus.

    Each vote (ranking=1 row per classifier) is weighted by:
      - method     — lc classifier > stamp, gap widens with ndet
      - confidence — classifier's own probability for its top class
      - recency    — small tiebreaker from classifier version string

    QA rules applied in order:
      >= HIGH_CONFIDENCE_THRESHOLD         → clean, no flag
      >= MAJORITY_THRESHOLD + outliers low → majority label, minor flag
      otherwise                            → genuine split, needs review

    Receives an already-fetched probs DataFrame and ndet (not an oid)
    so no extra API calls are made here.
    """
    if probs.empty:
        return {"top_class": None, "class_prob": None, "consensus": None,
                "n_classifiers": 0, "n_agree": 0, "n_disagree": 0,
                "flag": "no_classification", "verdict": "review_major"}

    top = probs[probs["ranking"] == 1].copy()
    if top.empty:
        return {"top_class": None, "class_prob": None, "consensus": None,
                "n_classifiers": 0, "n_agree": 0, "n_disagree": 0,
                "flag": "no_ranking1_rows", "verdict": "review_major"}

    top["_w"] = (
        top["classifier_name"].apply(lambda n: _method_weight(n, ndet))
        * top["probability"]
        * top["classifier_version"].apply(_version_score)
    )

    class_weights = top.groupby("class_name")["_w"].sum()
    total_weight  = class_weights.sum()

    if total_weight == 0:
        return {"top_class": None, "class_prob": None, "consensus": None,
                "n_classifiers": len(top), "n_agree": 0, "n_disagree": 0,
                "flag": "zero_total_weight", "verdict": "review_major"}

    plurality    = class_weights.idxmax()
    w_consensus  = class_weights[plurality] / total_weight
    n_total      = len(top)
    n_agree      = (top["class_name"] == plurality).sum()
    n_classes    = top["class_name"].nunique()
    best_prob    = top[top["class_name"] == plurality]["probability"].max()

    dissenters    = top[top["class_name"] != plurality]
    outliers_only = dissenters.empty or (dissenters["probability"] < OUTLIER_PROB_THRESHOLD).all()

    if w_consensus >= HIGH_CONFIDENCE_THRESHOLD:
        flag    = None
        verdict = "pass"

    elif w_consensus >= MAJORITY_THRESHOLD and outliers_only:
        flag = (
            f"minor disagreement: {n_agree}/{n_total} agree on '{plurality}'; "
            f"dissenters all below prob {OUTLIER_PROB_THRESHOLD}"
        )
        verdict = "review_minor"

    else:
        top3 = class_weights.sort_values(ascending=False).head(3)
        breakdown = ", ".join(f"'{c}' {w/total_weight:.0%}" for c, w in top3.items())
        flag = (
            f"genuine split across {n_classes} classes "
            f"(weighted: {breakdown}) — needs review"
        )
        verdict = "review_major"

    return {
        "top_class":     plurality,
        "class_prob":    float(best_prob),
        "consensus":     float(w_consensus),
        "n_classifiers": n_total,
        "n_agree":       int(n_agree),
        "n_disagree":    int(n_total - n_agree),
        "flag":          flag,
        "verdict":       verdict,
    }
