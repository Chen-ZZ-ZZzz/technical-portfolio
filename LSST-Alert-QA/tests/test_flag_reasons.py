"""
flag_reasons (2026-11-04): every part of a row's flag carries a fixed key, set where the
flag is made, so a run's flagged count splits by reason (__main__.flag_split) and the CSV
records the split. Every flag path of both pipelines runs here through its real validator,
classifier and row builder.

What the two properties catch: dropping a flag_reason from any classifier path below turns
its case red (the key shows as FLAG_UNKEYED); a new path is caught only once a case here
reaches it, so a new flag needs its case.
"""

import re

import pandas as pd
import pytest

from rubin_qa import classifier
from rubin_qa.classifier import classify_antares, classify_object
from rubin_qa.reporting import FLAG_UNKEYED, build_antares_qa_row, build_qa_row
from rubin_qa.validators import validate_antares, validate_completeness
from tests.conftest import ATAT, BHRF, ZTF_STAMP_2025, class_rows, probs_frame

LSST_STAMP_201 = ("stamp_classifier_rubin_beta", "201")  # retired: context, never a vote
NEW_BHRF = ("lc_classifier_BHRF_forced_phot", "2.2.0")  # a version the registry lacks
KEY = re.compile(r"^[a-z0-9_]+$")


def _ranked_below_1(rows: list[dict]) -> list[dict]:
    return [{**r, "ranking": r["ranking"] + 1} for r in rows]


# path -> (probabilities, expected flag_reasons)
ALERCE_PATHS = {
    "two families agree": (lambda: probs_frame(class_rows(BHRF, "SN", 0.95), class_rows(ATAT, "SN", 0.90)), []),
    "minor disagreement": (lambda: probs_frame(class_rows(BHRF, "SN", 0.75), class_rows(ZTF_STAMP_2025, "SN", 0.75),
                                               class_rows(ATAT, "AGN", 0.25)), ["minor_disagreement"]),
    "genuine split": (lambda: probs_frame(class_rows(BHRF, "SN", 0.60), class_rows(ATAT, "AGN", 0.55)),
                      ["genuine_split"]),
    "one family": (lambda: probs_frame(class_rows(BHRF, "SN", 0.95)), ["insufficient_classifiers"]),
    "no probabilities": (lambda: pd.DataFrame(), ["no_classification", "insufficient_classifiers"]),
    "no ranking-1 row at all": (lambda: probs_frame(_ranked_below_1(class_rows(BHRF, "SN", 0.9))),
                                ["insufficient_classifiers", "no_ranking1_rows"]),
    "no ranking-1 row for the voter": (lambda: probs_frame(class_rows(LSST_STAMP_201, "SN", 0.9),
                                                           _ranked_below_1(class_rows(BHRF, "SN", 0.9))),
                                       ["insufficient_classifiers", "no_ranking1_rows"]),
    "only a retired version": (lambda: probs_frame(class_rows(LSST_STAMP_201, "SN", 0.99)),
                               ["insufficient_classifiers", "no_live_classifier"]),
    "zero total weight": (lambda: probs_frame(class_rows(BHRF, "SN", 0.0), class_rows(ATAT, "SN", 0.0)),
                          ["zero_total_weight"]),
    "two unknown versions": (lambda: probs_frame(class_rows(BHRF, "SN", 0.95), class_rows(NEW_BHRF, "SN", 0.95),
                                                 class_rows(("LC_classifier_ATAT_forced_phot(beta)", "1.1.0"),
                                                            "SN", 0.9)),
                             ["unknown_classifier", "insufficient_classifiers"]),
}

ANTARES_PATHS = {
    "one science tag": (["nuclear_transient", "lc_feature_extractor"], []),
    "two science tags": (["dimmers", "extragalactic"], ["multiple_science_tags"]),
    "pipeline tags only": (["lc_feature_extractor", "high_snr"], ["no_science_tags"]),
    "an unknown tag only": (["brand_new_filter_2027"], ["no_science_tags"]),
    "no tags": ([], ["no_classification"]),
}


def _alerce_row(data_complete, probs, ms=None):
    data = {**data_complete, "probs": probs, **({"ms": ms} if ms is not None else {})}
    cl = classify_object(probs, ndet=8)
    return build_qa_row("ZTF1", data, validate_completeness(data, survey="ztf"), cl, survey="ztf")


def _antares_row(antares_data, tags):
    data = {**antares_data, "tags": tags}
    return build_antares_qa_row("ANT1", data, validate_antares(data), classify_antares(tags))


def _rows(data_complete, antares_data):
    ms_one = pd.DataFrame({"ndet": [1], "magmin": [18.0], "magmax": [19.0], "fid": [1]})
    for name, (probs, keys) in ALERCE_PATHS.items():
        yield f"ALeRCE: {name}", _alerce_row(data_complete, probs()), keys
    yield ("ALeRCE: a completeness issue beside a split",
           _alerce_row(data_complete, ALERCE_PATHS["genuine split"][0](), ms=ms_one), ["ndet_lt_2", "genuine_split"])
    for name, (tags, keys) in ANTARES_PATHS.items():
        yield f"ANTARES: {name}", _antares_row(antares_data, tags), keys


def test_a_row_is_flagged_if_and_only_if_its_flag_reasons_is_not_empty(data_complete, antares_data):
    for name, row, _ in _rows(data_complete, antares_data):
        assert (row["flag"] is not None) == bool(row["flag_reasons"]), name


def test_every_flag_path_carries_its_own_fixed_keys(data_complete, antares_data):
    for name, row, keys in _rows(data_complete, antares_data):
        assert row["flag_reasons"] == keys, name
        assert FLAG_UNKEYED not in row["flag_reasons"] and all(KEY.match(k) for k in row["flag_reasons"]), name


def test_the_unreachable_minor_tier_is_keyed_too(antares_data, monkeypatch):
    """REVIEW_MINOR is unreachable for ANTARES at today's threshold (README); its flag still needs a key."""
    monkeypatch.setattr(classifier, "MAJORITY_THRESHOLD", 0.4)
    assert _antares_row(antares_data, ["dimmers", "extragalactic"])["flag_reasons"] == ["minor_science_tags"]


@pytest.mark.parametrize("broker", ["alerce", "antares"])
def test_no_classification_is_named_once_by_its_one_owner_the_validator(data_complete, antares_data, broker):
    """A report without flag_reasons shows it twice, the validator's and the classifier's: '...no_classification; no_classification'."""
    row = (_alerce_row(data_complete, pd.DataFrame()) if broker == "alerce" else _antares_row(antares_data, []))
    assert row["flag"].count("no_classification") == 1
    assert row["flag_reasons"].count("no_classification") == 1
    assert row["status"] == "FLAG"


def test_a_flag_made_without_a_key_shows_as_unkeyed_never_dropped(data_complete):
    """The builder's side of the guard: an unkeyed flag stays visible in the row and in the run's split."""
    cl = {**classify_object(ALERCE_PATHS["two families agree"][0](), ndet=8), "flag": "a new flag", "verdict": "review_major"}
    cl.pop("flag_reason")
    row = build_qa_row("ZTF1", data_complete, [], cl, survey="ztf")
    assert row["flag_reasons"] == [FLAG_UNKEYED]
