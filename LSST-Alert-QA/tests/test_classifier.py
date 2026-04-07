"""Tests for rubin_qa.classifier."""

import pandas as pd
import pytest

from rubin_qa.classifier import _method_weight, _version_score, classify_object


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
