"""Weighted classifier consensus — maps a probabilities DataFrame to a QA verdict."""

import re

import pandas as pd

# Consensus thresholds: tuning of this module's logic, so they live here, not in config.py
# (user, 2026-10-04).
HIGH_CONFIDENCE_THRESHOLD = 0.90   # weighted consensus → clean label, no flag
MAJORITY_THRESHOLD        = 0.65   # weighted consensus → majority, minor flag
OUTLIER_PROB_THRESHOLD    = 0.30   # dissenting classifier below this → outlier, not genuine split

# ANTARES tag taxonomy (from get_available_tags() as of 2026-04). Mostly ZTF filters;
# the Rubin ones then were young_rubin_transients (science) and
# lsst_scimma_quality_transient (pipeline). Re-read get_available_tags() for new ones:
# an unknown tag is flagged in no_science_tags, never silently scored.
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
    No tags     → no verdict and no flag: validate_antares reports no_classification,
                  its one owner (2026-11-04), so a row names it once.

    Return shape matches classify_object so build_antares_qa_row can use it. Every flag
    comes with its flag_reason, a fixed key (the row's flag_reasons, build_antares_qa_row).
    """
    science = [t for t in tags if t in SCIENCE_TAGS]

    if not science:
        pipeline_only = [t for t in tags if t in PIPELINE_TAGS]
        unknown = [t for t in tags if t not in SCIENCE_TAGS and t not in PIPELINE_TAGS]
        tagged = bool(pipeline_only or unknown)
        return {
            "top_class": None, "class_prob": None, "consensus": None,
            "n_classifiers": 0, "n_agree": 0, "n_disagree": 0,
            "flag": f"no_science_tags (pipeline: {pipeline_only}; unknown: {unknown})" if tagged else None,
            "flag_reason": "no_science_tags" if tagged else None,
            "verdict": "review_major",
        }

    science = sorted(science)
    n = len(science)
    top_class = ", ".join(science)
    consensus = round(1.0 / n, 4)

    if n == 1:
        flag, reason, verdict = None, None, "pass"
    elif consensus >= MAJORITY_THRESHOLD:
        flag    = f"minor: {n} science tags — {', '.join(science)}"
        reason, verdict = "minor_science_tags", "review_minor"
    else:
        flag    = f"multiple science tags ({n}): {', '.join(science)}"
        reason, verdict = "multiple_science_tags", "review_major"

    return {
        "top_class":     top_class,
        "class_prob":    1.0,
        "consensus":     consensus,
        "n_classifiers": n,          # science tags only
        "n_agree":       1,
        "n_disagree":    n - 1,
        "flag":          flag,
        "flag_reason":   reason,
        "verdict":       verdict,
    }


# ALeRCE classifier registry (user, 2026-11-03; the rule: CLAUDE.md, "QA pipeline"). Every
# (classifier_name, classifier_version) ALeRCE returns is listed: as a live version of a
# voting family, or as context. A family is one line of evidence, the same input and the same
# lineage of method, whatever the name or version. Only a live version votes, one vote per
# family; live means present on the newest objects (the test and its findings: README,
# "Independent Evidence in ALeRCE's Classifiers"). A pair listed nowhere is unknown: FLAG
# unknown_classifier, never a vote, until its line is added here. When one appears, run
# tools/alerce_live_classifiers.py: it prints what to change here, and this file is edited
# by hand.
#
# VOTERS: family -> its live (name, version) pairs; where several are on one object, the last
# listed votes.
#   stamp_classifier_rubin_beta (LSST): the Rubin stamp CNN on the alert cutouts. _20260421
#                     (202) is its live version; 201 is retired.
#   stamp (ZTF):      the stamp CNN on the first alert's cutouts. 1.0.4 and the retrained
#                     2025_beta (which adds a satellite class) are both live; 2025_beta votes.
#   BHRF (ZTF):       the balanced hierarchical random forest. 2.1.0, on forced photometry, is
#                     live; its alert-photometry ancestor lc_classifier and the forced-photometry
#                     betas are the same lineage, retired.
#   ATAT (ZTF):       a transformer on forced photometry, a different method, so its own
#                     family. 1.0.0 is live.
VOTERS: dict[str, tuple[tuple[str, str], ...]] = {
    "stamp_classifier_rubin_beta": (("stamp_classifier_rubin_beta_20260421", "202"),),
    "stamp": (("stamp_classifier", "stamp_classifier_1.0.4"), ("stamp_classifier_2025_beta", "2.1.1_beta")),
    "BHRF": (("lc_classifier_BHRF_forced_phot", "2.1.0"),),
    "ATAT": (("LC_classifier_ATAT_forced_phot(beta)", "1.0.0"),),
}
FAMILY_OF: dict[tuple[str, str], str] = {key: family for family, keys in VOTERS.items() for key in keys}

# CONTEXT: known pairs that never vote, in three kinds.
#   RETIRED:           a family's earlier versions, absent from the newest objects, with their
#                      hierarchy levels. One model drifting is not a second opinion, and a stale
#                      version is not the broker's current view. On a newest object it would be
#                      live again.
#   HIERARCHY_LEVELS:  a live hierarchical model's top level (Periodic / Stochastic / Transient)
#                      and branches: coarser views of its own leaf row, with class lists that never
#                      match the leaf's, not opinions of their own. Live by nature.
#   POSITION_LABELS:   nuclear_classifier (LSST), nuclear or off-nuclear at probability 1.0. It
#                      answers where, not what; its label stays in the probabilities, out of the
#                      vote and out of the flag. Live by nature.
_LEVELS = ("_top", "_periodic", "_stochastic", "_transient")
RETIRED: frozenset[tuple[str, str]] = frozenset({
    ("stamp_classifier_rubin_beta", "201"),
    ("stamp_classifier", "stamp_classifier_1.0.0"),
    ("stamp_classifier", "1.0.1"),
    ("stamp_classifier_2025_beta", "2.0.0_beta"),
    ("stamp_classifier_2025_beta", "2.1.0_beta"),
    ("lc_classifier", "hierarchical_rf_1.1.0"),
    ("lc_classifier", "lc_classifier_1.1.13"),
    *((f"lc_classifier{level}", "hierarchical_rf_1.1.0") for level in _LEVELS),
    *(("LC_classifier_BHRF_forced_phot(beta)", v) for v in ("1.0.7", "1.1.1", "1.1.4", "1.1.5", "2.0.0")),
    *(("LC_classifier_ATAT_forced_phot(beta)", v) for v in ("0.2.0", "0.3.1", "0.3.4", "0.3.6")),
})
HIERARCHY_LEVELS: frozenset[tuple[str, str]] = frozenset(
    (f"lc_classifier_BHRF_forced_phot{level}", "2.1.0") for level in _LEVELS)
POSITION_LABELS: frozenset[tuple[str, str]] = frozenset({("nuclear_classifier", "1")})
CONTEXT: frozenset[tuple[str, str]] = RETIRED | HIERARCHY_LEVELS | POSITION_LABELS


def _version_str(version) -> str:
    """A version as the registry spells it: ALeRCE LSST sends integers (202), ZTF strings."""
    if isinstance(version, str):
        return version
    if version is None or pd.isna(version):
        return ""
    return str(int(version)) if float(version).is_integer() else str(version)


def _version_score(version) -> float:
    """
    Parse a classifier version into a small recency multiplier [0.90, 1.10].
    Acts only as a tiebreaker — never overrides method or probability weights.

    Accepts whatever the broker puts in the column: ALeRCE ZTF sends strings
    ("1.0.0"), ALeRCE LSST sends integers (201, 202). An isinstance(str) guard
    here used to send every LSST version to the neutral 1.0.

    Falsy input (None, "", 0) means "unknown version" and scores neutral rather
    than penalised; NaN stringifies to "nan", finds no digits, and lands there too.

    NOTE the 0.20 cap saturates at raw >= 10, so any purely integer scheme at or
    above v10 scores a flat 1.10. Because the score multiplies every classifier's
    weight and consensus is a ratio of those weights, a value shared by all rows
    cancels out — so LSST's 201-vs-202 still cannot be told apart. Making integer
    schemes discriminate means rescaling the cap, which would move ZTF's tuned
    weights too; left alone deliberately.
    """
    if not version:
        return 1.0
    nums = re.findall(r"\d+", str(version))
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


_NO_VOTE = {"top_class": None, "class_prob": None, "consensus": None,
            "n_classifiers": 0, "n_agree": 0, "n_disagree": 0, "verdict": "review_major",
            "flag": None, "flag_reason": None}


def classify_object(probs: pd.DataFrame, ndet: int) -> dict:
    """
    Consensus of the broker's independent classifications.

    The votes come from the registry (VOTERS, CONTEXT): one per family, its live version's
    ranking=1 row, the version listed last where two are on the object. Context rows never
    vote. A (name, version) the registry does not know never votes either; it is returned in
    "unknown", and build_qa_row FLAGs it. Families are compared only when their class lists
    match exactly, read from the object's own rows (a version lists every class it has), so
    no class name is ever mapped onto another. The largest such group is judged (a tie goes
    to the heavier one); a group of one is a single opinion, n_classifiers 1.

    Within the group, each vote is weighted by:
      - method     — lc classifier > stamp, gap widens with ndet
      - confidence — the vote's own probability for its top class
      - recency    — small tiebreaker from classifier version string

    QA rules applied in order:
      >= HIGH_CONFIDENCE_THRESHOLD         → clean, no flag
      >= MAJORITY_THRESHOLD + outliers low → majority label, minor flag
      otherwise                            → genuine split, needs review

    Also returns "families" (the families with a live vote on this object), "compared"
    (those in the judged group) and "unknown" ("name@version" pairs). Every flag comes
    with its flag_reason, a fixed key (the row's flag_reasons, build_qa_row). Empty
    probabilities get no verdict and no flag: validate_completeness reports
    no_classification, its one owner (2026-11-04), so a row names it once.

    Receives an already-fetched probs DataFrame and ndet (not an oid)
    so no extra API calls are made here.
    """
    if probs.empty:
        return {**_NO_VOTE, "families": [], "compared": [], "unknown": []}

    probs = probs.assign(_key=[(str(n), _version_str(v))
                               for n, v in zip(probs["classifier_name"], probs["classifier_version"])])
    present = set(probs["_key"])
    unknown = sorted(f"{n}@{v}" for n, v in present if (n, v) not in FAMILY_OF and (n, v) not in CONTEXT)
    votes = [(family, [k for k in keys if k in present][-1])
             for family, keys in VOTERS.items() if any(k in present for k in keys)]
    seen = {"families": [family for family, _ in votes], "unknown": unknown}

    ranked = probs[probs["ranking"] == 1]
    if ranked.empty:
        return {**_NO_VOTE, "flag": "no_ranking1_rows", "flag_reason": "no_ranking1_rows", "compared": [], **seen}
    if not votes:
        return {**_NO_VOTE, "flag": "no_live_classifier", "flag_reason": "no_live_classifier", "compared": [], **seen}

    groups: dict[frozenset, list[dict]] = {}
    for family, key in votes:
        row = ranked[ranked["_key"] == key]
        if row.empty:
            continue
        row = row.iloc[0]
        weight = (_method_weight(row["classifier_name"], ndet) * row["probability"]
                  * _version_score(row["classifier_version"]))
        classes = frozenset(probs.loc[probs["_key"] == key, "class_name"])
        groups.setdefault(classes, []).append({"family": family, "class_name": row["class_name"],
                                               "probability": float(row["probability"]), "_w": float(weight)})
    if not groups:
        return {**_NO_VOTE, "flag": "no_ranking1_rows", "flag_reason": "no_ranking1_rows", "compared": [], **seen}
    group = max(groups.values(), key=lambda g: (len(g), sum(v["_w"] for v in g)))
    seen["compared"] = [v["family"] for v in group]
    top = pd.DataFrame(group)

    class_weights = top.groupby("class_name")["_w"].sum()
    total_weight  = class_weights.sum()

    if total_weight == 0:
        return {**_NO_VOTE, "n_classifiers": len(top), "flag": "zero_total_weight", "flag_reason": "zero_total_weight",
                **seen}

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
        reason  = None
        verdict = "pass"

    elif w_consensus >= MAJORITY_THRESHOLD and outliers_only:
        flag = (
            f"minor disagreement: {n_agree}/{n_total} agree on '{plurality}'; "
            f"dissenters all below prob {OUTLIER_PROB_THRESHOLD}"
        )
        reason  = "minor_disagreement"
        verdict = "review_minor"

    else:
        top3 = class_weights.sort_values(ascending=False).head(3)
        breakdown = ", ".join(f"'{c}' {w/total_weight:.0%}" for c, w in top3.items())
        flag = (
            f"genuine split across {n_classes} classes "
            f"(weighted: {breakdown}) — needs review"
        )
        reason  = "genuine_split"
        verdict = "review_major"

    return {
        "top_class":     plurality,
        "class_prob":    float(best_prob),
        "consensus":     float(w_consensus),
        "n_classifiers": n_total,
        "n_agree":       int(n_agree),
        "n_disagree":    int(n_total - n_agree),
        "flag":          flag,
        "flag_reason":   reason,
        "verdict":       verdict,
        **seen,
    }
