"""Tests for rubin_qa.reporting."""

import math
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from rubin_qa import reporting
from rubin_qa.config import OUTPUT_COLUMNS
from rubin_qa.reporting import _psfflux_to_mag, build_qa_row, run_pipeline


def _cl(top_class="SN", class_prob=0.95, consensus=0.95, n_classifiers=2,
        n_agree=2, n_disagree=0, flag=None, verdict="pass"):
    return dict(top_class=top_class, class_prob=class_prob, consensus=consensus,
                n_classifiers=n_classifiers, n_agree=n_agree, n_disagree=n_disagree,
                flag=flag, verdict=verdict)


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

    def test_no_usable_magnitude_column_degrades_to_nan(self, probs_clean):
        """
        Detections arrived, magstats did not, and the frame carries neither
        magpsf (ZTF) nor psfFlux (LSST) — a malformed response rather than a
        known-degraded survey. The row must still build: ndet and timespan come
        from the rows that are there, and only the magnitudes go NaN.
        """
        dets = pd.DataFrame({
            "ra":  [150.0, 150.1],
            "dec": [30.0, 30.1],
            "mjd": [59000.0, 59010.0],
        })
        data = {"dets": dets, "ms": pd.DataFrame(), "probs": probs_clean, "fetch_errors": []}
        row = build_qa_row("ZTF1", data, [], _cl())
        assert row["ndet"] == 2
        assert row["timespan_days"] == pytest.approx(10.0)
        assert math.isnan(row["mag_range"])
        assert row["confirmed"] is True

    def test_empty_magpsf_column_falls_through_to_psfflux(self, probs_clean):
        """
        magpsf is present but entirely null, and psfFlux has the real values.
        The `not isna().all()` arm of the guard is what lets this frame reach the
        flux path — simplify it to a bare `"magpsf" in columns` and this row goes
        NaN despite carrying usable magnitudes.
        """
        dets = pd.DataFrame({
            "ra":      [150.0, 150.1],
            "dec":     [30.0, 30.1],
            "magpsf":  [float("nan"), float("nan")],
            "psfFlux": [1000.0, 1200.0],
            "mjd":     [59000.0, 59010.0],
        })
        data = {"dets": dets, "ms": pd.DataFrame(), "probs": probs_clean, "fetch_errors": []}
        row = build_qa_row("ZTF1", data, [], _cl())
        assert row["mag_range"] == pytest.approx(0.198, abs=1e-3)

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

    def test_status_review_major_on_genuine_split(self, data_complete):
        flag = "genuine split across 2 classes (weighted: 'SN' 55%, 'AGN' 45%) — needs review"
        row = build_qa_row("ZTF1", data_complete, [], _cl(flag=flag, verdict="review_major"))
        assert row["status"] == "REVIEW_MAJOR"

    def test_status_flag_when_single_classifier(self, data_complete):
        row = build_qa_row("ZTF1", data_complete, [], _cl(n_classifiers=1, n_agree=1, n_disagree=0))
        assert row["status"] == "FLAG"
        assert "insufficient_classifiers" in row["flag"]

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


class TestRunPipeline:
    """
    The ALeRCE path end to end: fetch → validate → classify → row. The deadline
    tests in test_ceilings.py drive this loop too, but only with empty data, so
    every row they produce is a FLAG. These cover the composition on data that
    actually classifies, and the LSST degradation the ztf fixtures cannot show.
    """

    @staticmethod
    def _fetch(data):
        """A fetch_object_data stand-in that returns the same payload per oid."""
        return lambda oid, survey="ztf": data

    def test_one_row_per_object_with_full_schema(self, data_complete):
        with patch.object(reporting, "fetch_object_data", self._fetch(data_complete)):
            df = run_pipeline(
                oids=["ZTF1", "ZTF2", "ZTF3"], inter_object_delay=0, quiet=True,
            )
        assert len(df) == 3
        assert list(df.columns) == OUTPUT_COLUMNS
        assert list(df["oid"]) == ["ZTF1", "ZTF2", "ZTF3"]
        assert set(df["status"]) == {"PASS"}
        assert set(df["n_classifiers"]) == {2}

    def test_explicit_oids_skip_the_candidate_fetch(self, data_complete):
        fetch = MagicMock()
        with patch.object(reporting, "fetch_candidates", fetch), \
             patch.object(reporting, "fetch_object_data", self._fetch(data_complete)):
            run_pipeline(oids=["ZTF1"], inter_object_delay=0, quiet=True)
        fetch.assert_not_called()

    def test_candidate_fetch_used_when_no_oids_given(self, data_complete):
        fetch = MagicMock(return_value=["ZTF7", "ZTF8"])
        with patch.object(reporting, "fetch_candidates", fetch), \
             patch.object(reporting, "fetch_object_data", self._fetch(data_complete)):
            df = run_pipeline(page_size=2, inter_object_delay=0, quiet=True)
        assert list(df["oid"]) == ["ZTF7", "ZTF8"]
        assert fetch.call_args.kwargs["page_size"] == 2

    def test_survey_reaches_both_fetches(self, lsst_dets, probs_clean):
        """A survey typo that reaches only one of the two calls is a silent mix."""
        data = {"dets": lsst_dets, "ms": pd.DataFrame(),
                "probs": probs_clean, "fetch_errors": []}
        candidates = MagicMock(return_value=["1234"])
        objects = MagicMock(return_value=data)
        with patch.object(reporting, "fetch_candidates", candidates), \
             patch.object(reporting, "fetch_object_data", objects):
            run_pipeline(page_size=1, survey="lsst", inter_object_delay=0, quiet=True)
        assert candidates.call_args.kwargs["survey"] == "lsst"
        assert objects.call_args.kwargs["survey"] == "lsst"

    def test_empty_candidate_list_returns_typed_empty_frame(self):
        """
        __main__ writes no CSV and exits 1 on an empty frame, but reads df.columns
        on the way there — a bare DataFrame() would break that path.
        """
        with patch.object(reporting, "fetch_candidates", MagicMock(return_value=[])):
            df = run_pipeline(page_size=10, inter_object_delay=0, quiet=True)
        assert df.empty
        assert list(df.columns) == OUTPUT_COLUMNS

    def test_one_bad_object_does_not_abort_the_run(self, data_complete, ztf_ms, probs_clean):
        """One object's failed detections call costs one FLAG row, not the report."""
        broken = {"dets": pd.DataFrame(), "ms": ztf_ms, "probs": probs_clean,
                  "fetch_errors": ["detections:504 server error"]}

        def flaky(oid, survey="ztf"):
            return broken if oid == "ZTF2" else data_complete

        with patch.object(reporting, "fetch_object_data", flaky):
            df = run_pipeline(
                oids=["ZTF1", "ZTF2", "ZTF3"], inter_object_delay=0, quiet=True,
            )
        assert len(df) == 3
        assert list(df["status"]) == ["PASS", "FLAG", "PASS"]
        assert "fetch_error_detections" in df.iloc[1]["completeness_issues"]

    def test_lsst_falls_back_to_detections_without_magstats(self, lsst_dets, probs_clean):
        """
        query_magstats is not implemented for LSST, so ms arrives empty on every
        object and ndet/mag stats must come from the raw detections instead. The
        run still produces rows — degraded, not aborted.
        """
        data = {"dets": lsst_dets, "ms": pd.DataFrame(),
                "probs": probs_clean, "fetch_errors": []}
        with patch.object(reporting, "fetch_object_data", self._fetch(data)):
            df = run_pipeline(
                oids=["1234", "5678"], survey="lsst", inter_object_delay=0, quiet=True,
            )
        assert len(df) == 2
        assert list(df["ndet"]) == [2, 2]          # len(dets), not magstats
        assert df.iloc[0]["mag_range"] == pytest.approx(0.198, abs=1e-3)  # from psfFlux
        # Magstats being unavailable for LSST is a property of the API, not a
        # defect in the object, so it no longer forces FLAG: a clean LSST object
        # with two agreeing classifiers reaches PASS like any other.
        assert set(df["status"]) == {"PASS"}
        assert all(i == [] for i in df["completeness_issues"])

    def test_classifier_gets_ndet_from_magstats_not_row_count(self, data_complete):
        """
        ndet weights lc_classifier against stamp_classifier, so feeding it the
        3-row detections frame instead of the 8 magstats detections would quietly
        shift every verdict. The two differ in the fixture on purpose.
        """
        spy = MagicMock(side_effect=lambda probs, ndet: {
            "top_class": "SN", "class_prob": 0.9, "consensus": 0.95,
            "n_classifiers": 2, "n_agree": 2, "n_disagree": 0,
            "flag": None, "verdict": "pass",
        })
        with patch.object(reporting, "fetch_object_data", self._fetch(data_complete)), \
             patch.object(reporting, "classify_object", spy):
            run_pipeline(oids=["ZTF1"], inter_object_delay=0, quiet=True)
        assert len(data_complete["dets"]) == 3
        assert spy.call_args.args[1] == 8          # ztf_ms ndet column sums to 8

    def test_inter_object_delay_paces_the_loop(self, data_complete):
        """Rate-limit pacing is per object, and 0 must mean no sleep at all."""
        with patch.object(reporting, "fetch_object_data", self._fetch(data_complete)), \
             patch("rubin_qa.reporting.time.sleep") as mock_sleep:
            run_pipeline(oids=["ZTF1", "ZTF2"], inter_object_delay=0.5, quiet=True)
            assert [c.args[0] for c in mock_sleep.call_args_list] == [0.5, 0.5]
            mock_sleep.reset_mock()
            run_pipeline(oids=["ZTF1"], inter_object_delay=0, quiet=True)
            mock_sleep.assert_not_called()

    def test_progress_output_suppressed_by_quiet(self, data_complete, capsys):
        with patch.object(reporting, "fetch_object_data", self._fetch(data_complete)):
            run_pipeline(oids=["ZTF1"], inter_object_delay=0, quiet=True)
            assert capsys.readouterr().out == ""
            run_pipeline(oids=["ZTF1"], inter_object_delay=0, quiet=False)
            out = capsys.readouterr().out
            assert "ZTF1" in out and "PASS" in out
