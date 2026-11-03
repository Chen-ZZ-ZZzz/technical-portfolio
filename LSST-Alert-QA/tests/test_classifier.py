"""Tests for rubin_qa.classifier."""

import pandas as pd
import pytest

from rubin_qa import classifier
from rubin_qa.classifier import _method_weight, _version_score, classify_object
from rubin_qa.reporting import build_qa_row
from tests.conftest import ATAT, BHRF, LSST_STAMP, ZTF_STAMP, ZTF_STAMP_2025, class_rows, probs_frame

LSST_STAMP_201 = ("stamp_classifier_rubin_beta", "201")  # retired
NUCLEAR = ("nuclear_classifier", "1")

class TestVersionScore:
    def test_none_returns_one(self):
        assert _version_score(None) == 1.0

    def test_empty_string_returns_one(self):
        assert _version_score("") == 1.0

    def test_non_numeric_returns_one(self):
        assert _version_score("abc") == 1.0

    def test_valid_version_in_range(self):
        score = _version_score("1.2.3")
        assert 0.90 <= score <= 1.10

    def test_higher_version_scores_higher(self):
        assert _version_score("2.0.0") > _version_score("1.0.0")


class TestMethodWeight:
    def test_lc_classifier_outweighs_stamp_at_low_ndet(self):
        lc    = _method_weight("lc_classifier", ndet=10)
        stamp = _method_weight("stamp_classifier", ndet=10)
        assert lc > stamp

    def test_lc_weight_increases_with_ndet(self):
        assert _method_weight("lc_classifier", ndet=500) > _method_weight("lc_classifier", ndet=10)

    def test_stamp_weight_decreases_with_ndet(self):
        assert _method_weight("stamp_classifier", ndet=500) < _method_weight("stamp_classifier", ndet=10)

    def test_lc_max_weight_capped(self):
        assert _method_weight("lc_classifier", ndet=10_000) == pytest.approx(3.0)


class TestClassifyObject:
    def test_empty_probs_returns_no_classification(self):
        result = classify_object(pd.DataFrame(), ndet=5)
        assert result["flag"] == "no_classification"
        assert result["top_class"] is None

    def test_no_ranking1_rows(self):
        probs = pd.DataFrame({
            "classifier_name": ["lc_classifier"],
            "classifier_version": ["1.0"],
            "class_name": ["SN"],
            "probability": [0.9],
            "ranking": [2],
        })
        result = classify_object(probs, ndet=5)
        assert result["flag"] == "no_ranking1_rows"

    def test_high_confidence_no_flag(self, probs_clean):
        result = classify_object(probs_clean, ndet=10)
        assert result["flag"] is None
        assert result["top_class"] == "SN"
        assert result["consensus"] >= 0.90

    def test_majority_with_outlier_minor_flag(self, probs_majority):
        result = classify_object(probs_majority, ndet=10)
        assert result["flag"] is not None
        assert "minor disagreement" in result["flag"]
        assert result["top_class"] == "SN"

    def test_genuine_split_review_flag(self, probs_split):
        result = classify_object(probs_split, ndet=10)
        assert result["flag"] is not None
        assert "genuine split" in result["flag"]

    def test_n_agree_n_disagree_correct(self, probs_clean):
        result = classify_object(probs_clean, ndet=5)
        assert result["n_agree"] == 2
        assert result["n_disagree"] == 0
        assert result["n_classifiers"] == 2

    def test_class_prob_and_consensus_are_floats(self, probs_clean):
        result = classify_object(probs_clean, ndet=5)
        assert isinstance(result["class_prob"], float)
        assert isinstance(result["consensus"], float)


class TestFamilies:
    """One vote per model family, from its live version only (user, 2026-11-03; CLAUDE.md, QA pipeline).
    The registry is classifier.VOTERS / CONTEXT; the evidence: README, "Independent Evidence in
    ALeRCE's Classifiers"."""

    def test_two_live_versions_of_one_family_are_one_vote(self):
        probs = probs_frame(class_rows(ZTF_STAMP, "SN", 0.9), class_rows(ZTF_STAMP_2025, "SN", 0.9))
        cl = classify_object(probs, ndet=10)
        assert (cl["families"], cl["n_classifiers"]) == (["stamp"], 1)

    def test_when_both_stamp_versions_are_on_an_object_2025_beta_votes(self):
        probs = probs_frame(class_rows(ZTF_STAMP, "VS", 0.9), class_rows(ZTF_STAMP_2025, "SN", 0.8))
        assert classify_object(probs, ndet=10)["top_class"] == "SN"

    def test_a_retired_version_beside_the_live_one_adds_no_vote(self):
        """LSST objects first detected before the July stop carry the retired 201 beside 202."""
        probs = probs_frame(class_rows(LSST_STAMP_201, "SN", 0.99), class_rows(LSST_STAMP, "SN", 0.98))
        cl = classify_object(probs, ndet=10)
        assert (cl["families"], cl["n_classifiers"], cl["unknown"]) == (["stamp_classifier_rubin_beta"], 1, [])

    def test_an_object_with_only_retired_versions_has_no_vote(self):
        cl = classify_object(probs_frame(class_rows(LSST_STAMP_201, "SN", 0.99)), ndet=10)
        assert (cl["flag"], cl["n_classifiers"], cl["families"]) == ("no_live_classifier", 0, [])

    def test_nuclear_classifier_neither_votes_nor_reaches_the_flag(self, data_complete):
        probs = probs_frame(class_rows(LSST_STAMP, "SN", 0.98),
                            class_rows(NUCLEAR, "nuclear", 1.0, classes=("nuclear", "off-nuclear")))
        cl = classify_object(probs, ndet=10)
        assert (cl["families"], cl["n_classifiers"]) == (["stamp_classifier_rubin_beta"], 1)
        row = build_qa_row("1", {**data_complete, "probs": probs}, [], cl)
        assert row["status"] == "FLAG" and "nuclear" not in row["flag"]

    def test_an_unknown_classifier_flags_and_never_votes(self, data_complete):
        new_bhrf = ("lc_classifier_BHRF_forced_phot", "2.2.0")  # a known name, a version the registry lacks
        probs = probs_frame(class_rows(BHRF, "SN", 0.95), class_rows(new_bhrf, "SN", 0.95))
        cl = classify_object(probs, ndet=10)
        assert (cl["families"], cl["unknown"]) == (["BHRF"], ["lc_classifier_BHRF_forced_phot@2.2.0"])
        row = build_qa_row("1", {**data_complete, "probs": probs}, [], cl)
        assert row["status"] == "FLAG"
        assert "unknown_classifier: lc_classifier_BHRF_forced_phot@2.2.0" in row["flag"]

    def test_an_unknown_classifier_flags_even_beside_an_agreeing_pair(self, data_complete, probs_clean):
        probs = pd.concat([probs_clean, probs_frame(class_rows(("brand_new_classifier", "1.0"), "SN", 0.9))])
        cl = classify_object(probs, ndet=10)
        assert (cl["verdict"], cl["unknown"]) == ("pass", ["brand_new_classifier@1.0"])
        assert build_qa_row("1", {**data_complete, "probs": probs}, [], cl)["status"] == "FLAG"

    def test_families_are_compared_only_when_their_class_lists_match(self):
        """The live pair as ALeRCE has it: ATAT 1.0.0 lists SNIbc where BHRF 2.1.0 lists SESN."""
        common = ("SNIa", "SNII", "AGN", "QSO")
        bhrf = class_rows(BHRF, "SNIa", 0.9, classes=common + ("SESN",))
        cl = classify_object(probs_frame(bhrf, class_rows(ATAT, "SNIa", 0.9, classes=common + ("SNIbc",))), ndet=10)
        assert (cl["families"], cl["n_classifiers"], len(cl["compared"])) == (["BHRF", "ATAT"], 1, 1)
        cl = classify_object(probs_frame(bhrf, class_rows(ATAT, "SNIa", 0.9, classes=common + ("SESN",))), ndet=10)
        assert (cl["compared"], cl["n_classifiers"], cl["verdict"]) == (["BHRF", "ATAT"], 2, "pass")

    def test_the_registry_lists_no_pair_both_as_a_vote_and_as_context(self):
        assert not set(classifier.FAMILY_OF) & classifier.CONTEXT

    def test_the_three_kinds_of_context_are_disjoint(self):
        kinds = (classifier.RETIRED, classifier.HIERARCHY_LEVELS, classifier.POSITION_LABELS)
        assert sum(len(k) for k in kinds) == len(classifier.CONTEXT)

    @pytest.mark.parametrize("version", [202, 202.0, "202"])
    def test_lsst_integer_versions_match_the_registry(self, version):
        """ALeRCE LSST sends versions as integers; pandas may carry them as int64 or float64."""
        probs = probs_frame(class_rows(LSST_STAMP, "SN", 0.9)).assign(classifier_version=version)
        cl = classify_object(probs, ndet=10)
        assert (cl["families"], cl["unknown"]) == (["stamp_classifier_rubin_beta"], [])
