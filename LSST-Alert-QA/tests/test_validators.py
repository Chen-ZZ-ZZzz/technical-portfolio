"""Tests for alerce_qa.validators."""

import pandas as pd
import pytest

from alerce_qa.validators import validate_completeness


def _data(dets=None, ms=None, probs=None, fetch_errors=None):
    empty = pd.DataFrame()
    return {
        "dets":         empty if dets  is None else dets,
        "ms":           empty if ms    is None else ms,
        "probs":        empty if probs is None else probs,
        "fetch_errors": fetch_errors or [],
    }


class TestZTF:
    def test_complete_data_no_issues(self, data_complete):
        issues = validate_completeness(data_complete, survey="ztf")
        assert issues == []

    def test_no_detections(self):
        issues = validate_completeness(_data())
        assert "no_detections" in issues

    def test_no_detections_implies_no_magstats_and_no_classification(self):
        issues = validate_completeness(_data())
        assert "no_magstats" in issues
        assert "no_classification" in issues

    def test_ndet_lt_2_from_magstats(self, ztf_dets, probs_clean):
        ms = pd.DataFrame({"ndet": [1], "magmin": [18.0], "magmax": [19.0], "fid": [1]})
        issues = validate_completeness(_data(dets=ztf_dets, ms=ms, probs=probs_clean))
        assert "ndet_lt_2" in issues

    def test_ndet_lt_2_fallback_to_dets(self, probs_clean):
        one_det = pd.DataFrame({
            "ra": [150.0], "dec": [30.0], "magpsf": [18.5],
            "rb": [0.9], "drb": [0.8], "mjd": [59000.0],
        })
        issues = validate_completeness(_data(dets=one_det, probs=probs_clean))
        assert "ndet_lt_2" in issues

    def test_no_magstats_flagged(self, ztf_dets, probs_clean):
        issues = validate_completeness(_data(dets=ztf_dets, probs=probs_clean))
        assert "no_magstats" in issues

    def test_coordinates_missing_ra(self, ztf_dets, ztf_ms, probs_clean):
        dets = ztf_dets.drop(columns=["ra"])
        issues = validate_completeness(_data(dets=dets, ms=ztf_ms, probs=probs_clean))
        assert "coordinates_missing" in issues

    def test_coordinates_all_null(self, ztf_dets, ztf_ms, probs_clean):
        dets = ztf_dets.copy()
        dets["ra"] = None
        issues = validate_completeness(_data(dets=dets, ms=ztf_ms, probs=probs_clean))
        assert "coordinates_missing" in issues

    def test_mag_null(self, ztf_dets, ztf_ms, probs_clean):
        dets = ztf_dets.drop(columns=["magpsf"])
        issues = validate_completeness(_data(dets=dets, ms=ztf_ms, probs=probs_clean))
        assert "mag_null" in issues

    def test_rb_absent(self, ztf_dets, ztf_ms, probs_clean):
        dets = ztf_dets.drop(columns=["rb"])
        issues = validate_completeness(_data(dets=dets, ms=ztf_ms, probs=probs_clean))
        assert "rb_absent" in issues

    def test_drb_absent(self, ztf_dets, ztf_ms, probs_clean):
        dets = ztf_dets.drop(columns=["drb"])
        issues = validate_completeness(_data(dets=dets, ms=ztf_ms, probs=probs_clean))
        assert "drb_absent" in issues

    def test_no_classification(self, ztf_dets, ztf_ms):
        issues = validate_completeness(_data(dets=ztf_dets, ms=ztf_ms))
        assert "no_classification" in issues

    def test_fetch_error_recorded(self, ztf_dets, ztf_ms, probs_clean):
        data = _data(dets=ztf_dets, ms=ztf_ms, probs=probs_clean,
                     fetch_errors=["detections:timeout"])
        issues = validate_completeness(data)
        assert "fetch_error_detections" in issues


class TestLSST:
    def test_no_drb_for_lsst(self, lsst_dets, probs_clean):
        """drb_absent must never appear for LSST."""
        issues = validate_completeness(
            _data(dets=lsst_dets, probs=probs_clean), survey="lsst"
        )
        assert "drb_absent" not in issues

    def test_lsst_mag_null_when_no_psfFlux(self, lsst_dets, probs_clean):
        dets = lsst_dets.drop(columns=["psfFlux"])
        issues = validate_completeness(_data(dets=dets, probs=probs_clean), survey="lsst")
        assert "mag_null" in issues

    def test_lsst_mag_null_when_all_nonpositive(self, lsst_dets, probs_clean):
        dets = lsst_dets.copy()
        dets["psfFlux"] = -1.0
        issues = validate_completeness(_data(dets=dets, probs=probs_clean), survey="lsst")
        assert "mag_null" in issues

    def test_lsst_rb_absent_when_no_reliability(self, lsst_dets, probs_clean):
        dets = lsst_dets.drop(columns=["reliability"])
        issues = validate_completeness(_data(dets=dets, probs=probs_clean), survey="lsst")
        assert "rb_absent" in issues

    def test_lsst_no_magstats_always_fires(self, lsst_dets, probs_clean):
        """ms is always empty for LSST — no_magstats is expected/informational."""
        issues = validate_completeness(
            _data(dets=lsst_dets, probs=probs_clean), survey="lsst"
        )
        assert "no_magstats" in issues
