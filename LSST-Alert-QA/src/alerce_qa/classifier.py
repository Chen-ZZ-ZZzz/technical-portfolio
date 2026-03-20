"""Weighted classifier consensus — maps a probabilities DataFrame to a QA verdict."""

import re

import pandas as pd

from .config import HIGH_CONFIDENCE_THRESHOLD, MAJORITY_THRESHOLD, OUTLIER_PROB_THRESHOLD


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
                "flag": "no_classification"}

    top = probs[probs["ranking"] == 1].copy()
    if top.empty:
        return {"top_class": None, "class_prob": None, "consensus": None,
                "n_classifiers": 0, "n_agree": 0, "n_disagree": 0,
                "flag": "no_ranking1_rows"}

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
                "flag": "zero_total_weight"}

    plurality    = class_weights.idxmax()
    w_consensus  = class_weights[plurality] / total_weight
    n_total      = len(top)
    n_agree      = (top["class_name"] == plurality).sum()
    n_classes    = top["class_name"].nunique()
    best_prob    = top[top["class_name"] == plurality]["probability"].max()

    dissenters    = top[top["class_name"] != plurality]
    outliers_only = dissenters.empty or (dissenters["probability"] < OUTLIER_PROB_THRESHOLD).all()

    if w_consensus >= HIGH_CONFIDENCE_THRESHOLD:
        flag = None

    elif w_consensus >= MAJORITY_THRESHOLD and outliers_only:
        flag = (
            f"minor disagreement: {n_agree}/{n_total} agree on '{plurality}'; "
            f"dissenters all below prob {OUTLIER_PROB_THRESHOLD}"
        )

    else:
        top3 = class_weights.sort_values(ascending=False).head(3)
        breakdown = ", ".join(f"'{c}' {w/total_weight:.0%}" for c, w in top3.items())
        flag = (
            f"genuine split across {n_classes} classes "
            f"(weighted: {breakdown}) — needs review"
        )

    return {
        "top_class":     plurality,
        "class_prob":    float(best_prob),
        "consensus":     float(w_consensus),
        "n_classifiers": n_total,
        "n_agree":       int(n_agree),
        "n_disagree":    int(n_total - n_agree),
        "flag":          flag,
    }
