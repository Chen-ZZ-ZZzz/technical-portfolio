"""
Tests for the three-survey object profiler.

Offline: every broker call is stubbed. The profiler prints rather than returns,
so the contract under test is what an operator reads — which sections appear,
which numbers land in them, and that one broker's column names never leak into
another's branch.
"""

import sys
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from rubin_qa import profiler


@pytest.fixture
def ztf_lc():
    return {
        "detections": [
            {"mjd": 59000.0, "fid": 1, "magpsf": 18.5, "magpsf_corr": 18.4},
            {"mjd": 59010.0, "fid": 2, "magpsf": 19.0, "magpsf_corr": None},
        ],
        "non_detections": [{"mjd": 58990.0, "fid": 1, "diffmaglim": 20.1}],
    }


@pytest.fixture
def lsst_lc():
    return {
        "detections": [
            {"mjd": 61135.0, "band_name": "g", "psfFlux": 1000.0, "reliability": 0.9, "snr": 30.0},
            {"mjd": 61140.0, "band_name": "g", "psfFlux": 1200.0, "reliability": 0.7, "snr": 45.0},
            {"mjd": 61150.0, "band_name": "r", "psfFlux": 900.0, "reliability": 0.8, "snr": 22.0},
        ],
        "non_detections": [],
        "forced_photometry": [{"mjd": 61160.0, "band_name": "g", "psfFlux": 50.0}],
    }


class TestPerBand:
    def test_groups_and_summarises(self):
        dets = pd.DataFrame({
            "band": ["g", "g", "r"], "mag": [18.0, 19.0, 20.0],
            "mjd": [1.0, 3.0, 2.0],
        })
        table = profiler._per_band(dets, "band", "mag")
        assert list(table["band"]) == ["g", "r"]
        assert list(table["ndet"]) == [2, 1]
        assert table.iloc[0]["magmin"] == 18.0 and table.iloc[0]["magmax"] == 19.0
        assert table.iloc[0]["firstmjd"] == 1.0 and table.iloc[0]["lastmjd"] == 3.0

    def test_band_with_no_usable_magnitudes_is_dropped(self):
        dets = pd.DataFrame({"band": ["g", "r"], "mag": [18.0, float("nan")]})
        assert list(profiler._per_band(dets, "band", "mag")["band"]) == ["g"]

    def test_missing_columns_yield_an_empty_table(self):
        assert profiler._per_band(pd.DataFrame({"x": [1]}), "band", "mag").empty
        assert profiler._per_band(pd.DataFrame(), "band", "mag").empty


class TestSurveyValidation:
    def test_unknown_survey_is_refused(self):
        with pytest.raises(ValueError, match="survey must be one of"):
            profiler.object_profile("ZTF1", survey="nonesuch")

    def test_known_surveys_dispatch_to_their_own_branch(self):
        alerce, antares = MagicMock(), MagicMock()
        with patch.object(profiler, "_profile_alerce", alerce), \
             patch.object(profiler, "_profile_antares", antares):
            profiler.object_profile("ZTF1", survey="ztf")
            profiler.object_profile("1234", survey="lsst")
            profiler.object_profile("ANT1", survey="antares")
        assert [c.args for c in alerce.call_args_list] == [("ZTF1", "ztf"), ("1234", "lsst")]
        antares.assert_called_once_with("ANT1")


class TestProfileZtf:
    def _run(self, ztf_lc, probs, magstats):
        def api(fn, *args, **kwargs):
            return {"query_probabilities": (probs, None),
                    "query_magstats": (magstats, None),
                    "query_lightcurve": (ztf_lc, None)}[fn.__name__]
        with patch.object(profiler, "_api_call", api):
            profiler.object_profile("ZTF1", survey="ztf")

    def test_full_profile(self, ztf_lc, probs_clean, ztf_ms, capsys):
        self._run(ztf_lc, probs_clean, ztf_ms)
        out = capsys.readouterr().out
        assert "FULL PROFILE: ZTF1   [ztf]" in out
        assert "stamp_classifier" in out
        assert "Verdict:     SN" in out
        assert "Detections:     2 epochs" in out
        assert "Non-detections: 1 upper limits" in out
        assert "Corrected:      1 / 2 have magpsf_corr" in out

    def test_fid_is_translated_to_band_names(self, ztf_lc, probs_clean, ztf_ms, capsys):
        self._run(ztf_lc, probs_clean, ztf_ms)
        out = capsys.readouterr().out
        assert "Bands seen:     ['g', 'r']" in out
        assert "Forced phot." not in out       # ZTF lightcurves carry no forced photometry

    def test_missing_probabilities_does_not_abort_the_profile(self, ztf_lc, ztf_ms, capsys):
        self._run(ztf_lc, pd.DataFrame(), ztf_ms)
        out = capsys.readouterr().out
        assert "No probabilities available." in out
        assert "Detections:     2 epochs" in out   # later sections still run

    def test_unavailable_lightcurve_is_reported_not_raised(self, probs_clean, ztf_ms, capsys):
        def api(fn, *args, **kwargs):
            if fn.__name__ == "query_lightcurve":
                return None, "504 server error"
            return {"query_probabilities": (probs_clean, None),
                    "query_magstats": (ztf_ms, None)}[fn.__name__]
        with patch.object(profiler, "_api_call", api):
            profiler.object_profile("ZTF1", survey="ztf")
        assert "Light curve unavailable: 504 server error" in capsys.readouterr().out


class TestProfileLsst:
    def _run(self, lsst_lc, probs, capsys=None):
        dets = pd.DataFrame(lsst_lc["detections"])

        def api(fn, *args, **kwargs):
            name = fn.__name__
            if name == "query_magstats":
                raise AssertionError("LSST magstats must never be requested")
            return {"query_probabilities": (probs, None),
                    "query_detections": (dets, None),
                    "query_lightcurve": (lsst_lc, None)}[name]

        with patch.object(profiler, "_api_call", api):
            profiler.object_profile("1234", survey="lsst")

    def test_magstats_are_never_requested(self, lsst_lc, probs_clean):
        """ALeRCE raises NotImplementedError for these — asking wastes a call."""
        self._run(lsst_lc, probs_clean)   # the stub asserts if it is called

    def test_band_table_is_derived_from_flux(self, lsst_lc, probs_clean, capsys):
        self._run(lsst_lc, probs_clean)
        out = capsys.readouterr().out
        assert "derived from detections" in out
        # psfFlux 1000 nJy -> 23.9 AB; 1200 -> 23.702
        assert "23.702" in out and "23.9" in out

    def test_forced_photometry_is_surfaced(self, lsst_lc, probs_clean, capsys):
        self._run(lsst_lc, probs_clean)
        out = capsys.readouterr().out
        assert "Non-detections: 0 upper limits" in out
        assert "Forced phot.:   1 epochs" in out

    def test_quality_columns_reported(self, lsst_lc, probs_clean, capsys):
        self._run(lsst_lc, probs_clean)
        out = capsys.readouterr().out
        assert "Reliability:" in out and "SNR:" in out
        assert "magpsf_corr" not in out      # a ZTF-only concept

    def test_bands_come_from_band_name(self, lsst_lc, probs_clean, capsys):
        self._run(lsst_lc, probs_clean)
        assert "Bands seen:     ['g', 'r']" in capsys.readouterr().out


class TestProfileAntares:
    def _locus(self, tags, lc):
        locus = MagicMock()
        locus.tags = tags
        locus.lightcurve = lc
        return locus

    def _run(self, locus, locus_id="ANT1"):
        search = MagicMock()
        search.get_by_id.return_value = locus
        search.get_by_ztf_object_id.return_value = locus
        with patch("rubin_qa.antares_client._search", return_value=search), \
             patch("rubin_qa.antares_client._api_call",
                   lambda fn, *a, **k: (fn(*a, **k), None)):
            profiler.object_profile(locus_id, survey="antares")
        return search

    @pytest.fixture
    def lc(self):
        return pd.DataFrame({
            "ant_mag":           [18.5, 19.0, float("nan")],
            "ant_maglim":        [float("nan"), float("nan"), 20.5],
            "ant_mag_corrected": [18.4, float("nan"), float("nan")],
            "ant_passband":      ["g", "R", "g"],
            "ant_mjd":           [60000.0, 60010.0, 59990.0],
            "ant_survey":        [1, 1, 1],
        })

    def test_detections_and_upper_limits_are_separated(self, lc, capsys):
        self._run(self._locus(["nuclear_transient"], lc))
        out = capsys.readouterr().out
        assert "Detections:     2 epochs" in out
        assert "Non-detections: 1 upper limits" in out

    def test_upper_limits_are_excluded_from_the_band_table(self, lc, capsys):
        """The ant_maglim row has no ant_mag and must not enter the statistics."""
        self._run(self._locus(["nuclear_transient"], lc))
        out = capsys.readouterr().out
        assert "20.5" not in out
        assert "Corrected:      1 / 2 have ant_mag_corrected" in out

    def test_science_tag_produces_a_verdict(self, lc, capsys):
        self._run(self._locus(["nuclear_transient"], lc))
        out = capsys.readouterr().out
        assert "Raw tags (1): nuclear_transient" in out
        assert "Verdict:     nuclear_transient" in out

    def test_pipeline_only_tags_report_no_classification(self, lc, capsys):
        self._run(self._locus(["lc_feature_extractor"], lc))
        out = capsys.readouterr().out
        assert "No classification." in out
        assert "Detections:     2 epochs" in out   # the rest of the profile still runs

    def test_ztf_id_routes_to_the_ztf_lookup(self, lc):
        search = self._run(self._locus(["dimmers"], lc), locus_id="ZTF20aafqubg")
        search.get_by_ztf_object_id.assert_called_once()
        search.get_by_id.assert_not_called()

    def test_missing_locus_is_reported_not_raised(self, capsys):
        search = MagicMock()
        with patch("rubin_qa.antares_client._search", return_value=search), \
             patch("rubin_qa.antares_client._api_call",
                   lambda fn, *a, **k: (None, "locus:500")):
            profiler.object_profile("ANT1", survey="antares")
        assert "Locus unavailable: locus:500" in capsys.readouterr().out

    def test_empty_lightcurve_degrades(self, capsys):
        self._run(self._locus(["dimmers"], pd.DataFrame()))
        assert "Light curve unavailable." in capsys.readouterr().out


class TestCli:
    def test_profiles_each_oid(self):
        spy = MagicMock()
        with patch.object(sys, "argv", ["prof", "lsst", "111", "222"]), \
             patch.object(profiler, "object_profile", spy):
            profiler.main()
        assert [c.args[0] for c in spy.call_args_list] == ["111", "222"]
        assert spy.call_args.kwargs["survey"] == "lsst"

    def test_defaults_to_ztf(self):
        spy = MagicMock()
        with patch.object(sys, "argv", ["prof", "ZTF1"]), \
             patch.object(profiler, "object_profile", spy):
            profiler.main()
        assert spy.call_args.kwargs["survey"] == "ztf"

    def test_invalid_survey_is_rejected(self, capsys):
        with patch.object(sys, "argv", ["prof", "bogus", "X"]):
            with pytest.raises(SystemExit) as exc:
                profiler.main()
        assert exc.value.code == 2
        assert "invalid choice: 'bogus'" in capsys.readouterr().err

    def test_one_failure_does_not_stop_the_rest(self, capsys):
        spy = MagicMock(side_effect=[RuntimeError("boom"), None])
        with patch.object(sys, "argv", ["prof", "ztf", "ZTF1", "ZTF2"]), \
             patch.object(profiler, "object_profile", spy):
            profiler.main()
        assert spy.call_count == 2
        assert "ZTF1: profile failed — RuntimeError: boom" in capsys.readouterr().err
