"""Tests for alerce_qa.reporting."""

import math

import pandas as pd
import pytest

from alerce_qa.reporting import _psfflux_to_mag, build_qa_row


def _cl(top_class="SN", class_prob=0.95, consensus=0.95, n_classifiers=2,
        n_agree=2, n_disagree=0, flag=None):
    return dict(top_class=top_class, class_prob=class_prob, consensus=consensus,
                n_classifiers=n_classifiers, n_agree=n_agree, n_disagree=n_disagree,
                flag=flag)


class TestPsffluxToMag:
    def test_converts_positive_flux(self):
        flux = pd.Series([1000.0])
        mags = _psfflux_to_mag(flux)
        assert len(mags) == 1
        assert mags.iloc[0] == pytest.approx(-2.5 * math.log10(1000.0) + 31.4)

    def test_drops_nonpositive(self):
        flux = pd.Series([1000.0, 0.0, -50.0])
        mags = _psfflux_to_mag(flux)
        assert len(mags) == 1

    def test_drops_nan(self):
        flux = pd.Series([1000.0, float("nan")])
        mags = _psfflux_to_mag(flux)
        assert len(mags) == 1


class TestBuildQaRow:
    def test_uses_magstats_when_available(self, data_complete):
        row = build_qa_row("ZTF1", data_complete, [], _cl())
        assert row["ndet"] == 8           # 5 + 3
        assert row["mag_range"] == pytest.approx(19.2 - 17.5, abs=1e-3)

    def test_falls_back_to_dets_when_no_magstats(self, ztf_dets, probs_clean):
        data = {"dets": ztf_dets, "ms": pd.DataFrame(), "probs": probs_clean, "fetch_errors": []}
        row = build_qa_row("ZTF1", data, [], _cl())
        assert row["ndet"] == len(ztf_dets)
        assert not math.isnan(row["mag_range"])

    def test_lsst_fallback_uses_psfflux(self, lsst_dets, probs_clean):
        data = {"dets": lsst_dets, "ms": pd.DataFrame(), "probs": probs_clean, "fetch_errors": []}
        row = build_qa_row("100001", data, [], _cl())
        assert not math.isnan(row["mag_range"])

    def test_timespan_from_mjd(self, data_complete):
        row = build_qa_row("ZTF1", data_complete, [], _cl())
        # ztf_dets mjd: 59000, 59010, 59020 → span = 20.0
        assert row["timespan_days"] == pytest.approx(20.0)

    def test_timespan_nan_when_no_epoch_col(self, ztf_ms, probs_clean):
        dets = pd.DataFrame({"ra": [1.0, 2.0], "dec": [1.0, 2.0], "magpsf": [18.0, 19.0],
                             "rb": [0.9, 0.9], "drb": [0.8, 0.8]})
        data = {"dets": dets, "ms": ztf_ms, "probs": probs_clean, "fetch_errors": []}
        row = build_qa_row("ZTF1", data, [], _cl())
        assert math.isnan(row["timespan_days"])

    def test_confirmed_true_when_ndet_gt_1(self, data_complete):
        row = build_qa_row("ZTF1", data_complete, [], _cl())
        assert row["confirmed"] is True

    def test_confirmed_false_when_ndet_eq_1(self, ztf_dets, probs_clean):
        ms = pd.DataFrame({"ndet": [1], "magmin": [18.0], "magmax": [19.0], "fid": [1]})
        data = {"dets": ztf_dets, "ms": ms, "probs": probs_clean, "fetch_errors": []}
        row = build_qa_row("ZTF1", data, [], _cl())
        assert row["confirmed"] is False

    def test_status_pass_when_no_flag(self, data_complete):
        row = build_qa_row("ZTF1", data_complete, [], _cl(flag=None))
        assert row["status"] == "PASS"
        assert row["flag"] is None

    def test_status_review_on_genuine_split(self, data_complete):
        flag = "genuine split across 2 classes (weighted: 'SN' 55%, 'AGN' 45%) — needs review"
        row = build_qa_row("ZTF1", data_complete, [], _cl(flag=flag))
        assert row["status"] == "REVIEW"

    def test_status_flag_on_completeness_issues(self, data_complete):
        row = build_qa_row("ZTF1", data_complete, ["no_magstats"], _cl(flag=None))
        assert row["status"] == "FLAG"
        assert "completeness" in row["flag"]

    def test_flag_combines_issues_and_classifier_flag(self, data_complete):
        row = build_qa_row("ZTF1", data_complete, ["drb_absent"],
                           _cl(flag="minor disagreement: 2/3 agree on 'SN'"))
        assert "completeness" in row["flag"]
        assert "minor disagreement" in row["flag"]

    def test_empty_data_all_nan(self, data_empty):
        row = build_qa_row("ZTF1", data_empty, [], _cl())
        assert row["ndet"] == 0
        assert math.isnan(row["mag_range"])
        assert math.isnan(row["timespan_days"])

    def test_class_prob_and_consensus_rounded(self, data_complete):
        row = build_qa_row("ZTF1", data_complete, [], _cl(class_prob=0.123456, consensus=0.987654))
        assert row["class_prob"] == pytest.approx(0.1235, abs=1e-4)
        assert row["consensus"]  == pytest.approx(0.9877, abs=1e-4)

    def test_none_class_prob_stays_none(self, data_complete):
        row = build_qa_row("ZTF1", data_complete, [], _cl(class_prob=None, consensus=None))
        assert row["class_prob"] is None
        assert row["consensus"]  is None
