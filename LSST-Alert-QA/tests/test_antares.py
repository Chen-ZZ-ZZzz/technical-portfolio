"""
Tests for the ANTARES broker pipeline: client → validator → classifier → row → run.

The ALeRCE path had unit coverage from the start; ANTARES only had the ceiling and
CLI tests, which exercise the loop but never the locus model itself. What is checked
here is mostly the places where ANTARES differs from ALeRCE and where a silent wrong
answer is possible: the ANT/ZTF id routing, the upper-limit filter, num_mag_values
winning over len(dets), and the science/pipeline tag split.

Mock data only — no live API calls.
"""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import requests
from antares_client.exceptions import AntaresException

from rubin_qa.antares_client import (
    _api_call,
    _search,
    fetch_antares_candidates,
    fetch_antares_locus,
)
from rubin_qa.classifier import classify_antares
from rubin_qa.config import OUTPUT_COLUMNS, RETRY_ATTEMPTS, RETRY_DELAY
from rubin_qa.reporting import build_antares_qa_row, run_antares_pipeline
from rubin_qa.validators import validate_antares


def make_alert(mjd: float, mag: float | None, **extra) -> SimpleNamespace:
    """
    One locus alert. ant_mag=None stands in for a ztf_upper_limit row: ANTARES
    carries non-detections in locus.alerts with no magnitude attached.
    """
    props = {"ant_mag": mag, "ant_passband": "R"}
    props.update(extra)
    return SimpleNamespace(mjd=mjd, properties=props)


def make_locus(
    locus_id: str = "ANT1",
    ra: float = 153.5,
    dec: float = -3.42,
    alerts: list | None = None,
    properties: dict | None = None,
    tags: list | None = None,
) -> SimpleNamespace:
    if alerts is None:
        alerts = [make_alert(60000.0, 18.9), make_alert(60012.0, 18.1)]
    if properties is None:
        properties = {
            "num_mag_values": 2,
            "brightest_alert_magnitude": 18.1,
            "faintest_alert_magnitude": 18.9,
        }
    if tags is None:
        tags = ["nuclear_transient"]
    return SimpleNamespace(
        locus_id=locus_id, ra=ra, dec=dec,
        alerts=alerts, properties=properties, tags=tags,
    )


class ExplodingLocus(SimpleNamespace):
    """
    Locus whose named attributes raise on access. antares-client builds these
    lazily, so one bad field is a live possibility — the question is whether it
    costs one column or the whole row.
    """

    def __init__(self, broken: set, **kw):
        super().__init__(**kw)
        object.__setattr__(self, "_broken", broken)

    def __getattribute__(self, name):
        if name != "_broken" and name in object.__getattribute__(self, "_broken"):
            raise RuntimeError(f"{name} blew up")
        return object.__getattribute__(self, name)


def patch_search(**attrs):
    """Patch the lazily imported antares_client.search module with a mock."""
    mock = MagicMock()
    for name, value in attrs.items():
        setattr(mock, name, value)
    return patch("rubin_qa.antares_client._search", return_value=mock), mock


class TestApiCall:
    def test_success_returns_result(self):
        fn = MagicMock(return_value="ok")
        result, err = _api_call(fn, "ANT1")
        assert result == "ok"
        assert err is None

    def test_requests_exception_retries_then_fails(self, capsys):
        fn = MagicMock(side_effect=requests.exceptions.ReadTimeout("timed out"))
        fn.__name__ = "get_by_id"
        with patch("rubin_qa.antares_client.time.sleep") as mock_sleep:
            result, err = _api_call(fn, "ANT1")
        assert result is None
        assert "timed out" in err
        # Same shape as the ALeRCE client: N attempts → N-1 exponential backoffs.
        expected = [RETRY_DELAY * 2 ** i for i in range(RETRY_ATTEMPTS - 1)]
        assert [c.args[0] for c in mock_sleep.call_args_list] == expected
        assert capsys.readouterr().err.count("WARN: get_by_id failed") == RETRY_ATTEMPTS - 1

    def test_antares_exception_is_retried(self):
        """A missing ANT id answers 500, not 404 — indistinguishable from a fault."""
        fn = MagicMock(side_effect=AntaresException("500 server error"))
        fn.__name__ = "get_by_id"
        with patch("rubin_qa.antares_client.time.sleep"):
            result, err = _api_call(fn, "ANT_NOPE")
        assert result is None
        assert "500" in err
        # Full backoff spent on an id that simply does not exist — the cost of
        # ANTARES answering 500 where 404 belongs.
        assert fn.call_count == RETRY_ATTEMPTS


class TestFetchAntaresCandidates:
    def test_deduplicates_locus_ids_preserving_order(self):
        ids = ["ANT1", "ANT2", "ANT1", "ANT3", "ANT2"]
        patcher, _ = patch_search(get_random_locus_ids=MagicMock(return_value=ids))
        with patcher:
            result = fetch_antares_candidates(page_size=5)
        # The dedup loss is upstream (unseeded random_score, per-page requests),
        # so a short list is expected behaviour, not a dropped row.
        assert result == ["ANT1", "ANT2", "ANT3"]

    def test_returns_empty_list_on_error(self, capsys):
        fn = MagicMock(side_effect=requests.exceptions.ConnectionError("down"))
        fn.__name__ = "get_random_locus_ids"
        patcher, _ = patch_search(get_random_locus_ids=fn)
        with patcher, patch("rubin_qa.antares_client.time.sleep"):
            result = fetch_antares_candidates(page_size=10)
        assert result == []
        assert "ERROR: fetch_antares_candidates" in capsys.readouterr().err

    def test_none_result_is_not_an_error(self):
        patcher, _ = patch_search(get_random_locus_ids=MagicMock(return_value=None))
        with patcher:
            assert fetch_antares_candidates(page_size=10) == []


class TestFetchAntaresLocus:
    def test_ant_id_routes_to_get_by_id(self):
        by_id, by_ztf = MagicMock(return_value=make_locus()), MagicMock()
        patcher, _ = patch_search(get_by_id=by_id, get_by_ztf_object_id=by_ztf)
        with patcher:
            fetch_antares_locus("ANT2020hnj2u")
        by_id.assert_called_once_with("ANT2020hnj2u")
        by_ztf.assert_not_called()

    def test_ztf_id_routes_to_get_by_ztf_object_id(self):
        by_id, by_ztf = MagicMock(), MagicMock(return_value=make_locus())
        patcher, _ = patch_search(get_by_id=by_id, get_by_ztf_object_id=by_ztf)
        with patcher:
            fetch_antares_locus("ZTF20aafqubg")
        by_ztf.assert_called_once_with("ZTF20aafqubg")
        by_id.assert_not_called()

    def test_upper_limits_are_excluded_from_lightcurve(self):
        """ztf_upper_limit alerts ride in locus.alerts with no ant_mag."""
        locus = make_locus(alerts=[
            make_alert(60000.0, 18.9),
            make_alert(60003.0, None),   # upper limit
            make_alert(60012.0, 18.1),
        ])
        patcher, _ = patch_search(get_by_id=MagicMock(return_value=locus))
        with patcher:
            data = fetch_antares_locus("ANT1")
        assert len(data["dets"]) == 2
        assert data["dets"]["ant_mag"].notna().all()

    def test_ndet_prefers_num_mag_values_over_row_count(self):
        """
        ANTARES applies quality cuts the raw alert stream does not reflect, so
        num_mag_values sits below len(dets). Taking the row count instead would
        silently overstate every ndet in the report.
        """
        locus = make_locus(
            alerts=[make_alert(60000.0 + i, 19.0) for i in range(5)],
            properties={
                "num_mag_values": 3,
                "brightest_alert_magnitude": 18.1,
                "faintest_alert_magnitude": 19.4,
            },
        )
        patcher, _ = patch_search(get_by_id=MagicMock(return_value=locus))
        with patcher:
            data = fetch_antares_locus("ANT1")
        assert len(data["dets"]) == 5
        assert int(data["ms"]["ndet"].iloc[0]) == 3

    def test_ms_falls_back_to_row_count_without_num_mag_values(self):
        locus = make_locus(properties={"brightest_alert_magnitude": 18.1})
        patcher, _ = patch_search(get_by_id=MagicMock(return_value=locus))
        with patcher:
            data = fetch_antares_locus("ANT1")
        assert int(data["ms"]["ndet"].iloc[0]) == 2
        assert pd.isna(data["ms"]["magmax"].iloc[0])

    def test_locus_coordinates_are_copied_onto_every_alert(self):
        locus = make_locus(ra=10.5, dec=-2.25)
        patcher, _ = patch_search(get_by_id=MagicMock(return_value=locus))
        with patcher:
            dets = fetch_antares_locus("ANT1")["dets"]
        assert (dets["ra"] == 10.5).all()
        assert (dets["dec"] == -2.25).all()

    def test_missing_ztf_object_returns_not_found(self):
        patcher, _ = patch_search(get_by_ztf_object_id=MagicMock(return_value=None))
        with patcher:
            data = fetch_antares_locus("ZTF_missing")
        assert data["fetch_errors"] == ["locus:not_found"]
        assert data["dets"].empty and data["tags"] == []

    def test_fetch_failure_is_reported_as_locus_error(self):
        fn = MagicMock(side_effect=requests.exceptions.ReadTimeout("timed out"))
        fn.__name__ = "get_by_id"
        patcher, _ = patch_search(get_by_id=fn)
        with patcher, patch("rubin_qa.antares_client.time.sleep"):
            data = fetch_antares_locus("ANT1")
        assert data["fetch_errors"][0].startswith("locus:")
        assert data["ms"].empty

    def test_alerts_are_optional(self):
        locus = make_locus(alerts=[])
        patcher, _ = patch_search(get_by_id=MagicMock(return_value=locus))
        with patcher:
            data = fetch_antares_locus("ANT1")
        assert data["dets"].empty
        assert data["fetch_errors"] == []
        assert int(data["ms"]["ndet"].iloc[0]) == 2


class TestDeferredImport:
    def test_missing_client_raises_an_actionable_error(self):
        """
        The import is deferred so ALeRCE-only users never need antares-client.
        If it is missing the message has to say what to install — this is the
        first thing an operator sees when the ANTARES survey is selected.
        """
        with patch.dict(sys.modules, {"antares_client": None}):
            with pytest.raises(ImportError, match="pip install antares-client"):
                _search()

    def test_returns_the_search_module_when_installed(self):
        """Every other test patches _search, so pin what it actually hands back."""
        import antares_client.search

        assert _search() is antares_client.search

    def test_import_happens_at_call_time_not_module_import(self):
        """
        rubin_qa.antares_client must import cleanly without the broker package,
        or importing reporting.py would break the ALeRCE path too.
        """
        import importlib

        with patch.dict(sys.modules, {"antares_client": None}):
            importlib.reload(importlib.import_module("rubin_qa.antares_client"))


class TestFetchAntaresLocusDegradation:
    """One unusable field must cost a column, not the row."""

    def test_broken_alerts_still_yield_magstats_and_tags(self):
        locus = ExplodingLocus(
            {"alerts"},
            locus_id="ANT1", ra=1.0, dec=2.0,
            properties={"num_mag_values": 4, "brightest_alert_magnitude": 18.0,
                        "faintest_alert_magnitude": 19.0},
            tags=["nuclear_transient"],
        )
        patcher, _ = patch_search(get_by_id=MagicMock(return_value=locus))
        with patcher:
            data = fetch_antares_locus("ANT1")
        assert data["fetch_errors"] == ["alerts:alerts blew up"]
        assert data["dets"].empty
        assert int(data["ms"]["ndet"].iloc[0]) == 4
        assert data["tags"] == ["nuclear_transient"]

    def test_broken_properties_still_yield_alerts_and_tags(self):
        locus = ExplodingLocus(
            {"properties"},
            locus_id="ANT1", ra=1.0, dec=2.0,
            alerts=[make_alert(60000.0, 18.9), make_alert(60010.0, 18.1)],
            tags=["dimmers"],
        )
        patcher, _ = patch_search(get_by_id=MagicMock(return_value=locus))
        with patcher:
            data = fetch_antares_locus("ANT1")
        assert data["fetch_errors"] == ["properties:properties blew up"]
        assert len(data["dets"]) == 2
        assert data["ms"].empty
        assert data["tags"] == ["dimmers"]

    def test_broken_tags_still_yield_the_lightcurve(self):
        locus = ExplodingLocus(
            {"tags"},
            locus_id="ANT1", ra=1.0, dec=2.0,
            alerts=[make_alert(60000.0, 18.9)],
            properties={"num_mag_values": 1},
        )
        patcher, _ = patch_search(get_by_id=MagicMock(return_value=locus))
        with patcher:
            data = fetch_antares_locus("ANT1")
        assert data["fetch_errors"] == ["tags:tags blew up"]
        assert len(data["dets"]) == 1

    def test_every_field_broken_degrades_without_raising(self):
        locus = ExplodingLocus(
            {"alerts", "properties", "tags"}, locus_id="ANT1", ra=1.0, dec=2.0,
        )
        patcher, _ = patch_search(get_by_id=MagicMock(return_value=locus))
        with patcher:
            data = fetch_antares_locus("ANT1")
        assert len(data["fetch_errors"]) == 3
        assert data["dets"].empty and data["ms"].empty and data["tags"] == []

    def test_degraded_locus_becomes_a_flag_row_not_a_crash(self):
        """
        End to end: a properties failure leaves no magstats, so the row builder
        falls back to counting alert rows and the validator flags the gap.
        """
        locus = ExplodingLocus(
            {"properties"},
            locus_id="ANT1", ra=1.0, dec=2.0,
            alerts=[make_alert(60000.0, 18.9), make_alert(60010.0, 18.1)],
            tags=["nuclear_transient"],
        )
        patcher, _ = patch_search(get_by_id=MagicMock(return_value=locus))
        with patcher:
            data = fetch_antares_locus("ANT1")
        issues = validate_antares(data)
        row = build_antares_qa_row("ANT1", data, issues, classify_antares(data["tags"]))
        assert "fetch_error_properties" in issues and "mag_null" in issues
        assert row["ndet"] == 2                      # from len(dets), ms is empty
        assert row["mag_range"] != row["mag_range"]  # NaN — no magstats to subtract
        assert row["timespan_days"] == pytest.approx(10.0)
        assert row["status"] == "FLAG"


class TestValidateAntares:
    def test_complete_locus_has_no_issues(self, antares_data):
        assert validate_antares(antares_data) == []

    def test_empty_locus_short_circuits(self, antares_data_empty):
        issues = validate_antares(antares_data_empty)
        assert issues == ["no_detections", "no_classification"]

    def test_single_detection_is_unconfirmed(self, antares_data):
        antares_data["ms"] = pd.DataFrame([{"ndet": 1, "magmin": 18.1, "magmax": 18.1}])
        assert "ndet_lt_2" in validate_antares(antares_data)

    def test_ndet_comes_from_ms_not_row_count(self, antares_data):
        """3 alert rows but ANTARES counted 1 usable magnitude → unconfirmed."""
        antares_data["ms"] = pd.DataFrame([{"ndet": 1, "magmin": 18.1, "magmax": 18.9}])
        assert len(antares_data["dets"]) == 3
        assert "ndet_lt_2" in validate_antares(antares_data)

    def test_missing_coordinates_flagged(self, antares_data):
        antares_data["dets"] = antares_data["dets"].drop(columns=["dec"])
        assert "coordinates_missing" in validate_antares(antares_data)

    def test_missing_magnitudes_flagged(self, antares_data):
        antares_data["ms"] = pd.DataFrame([{"ndet": 3, "magmin": None, "magmax": None}])
        assert "mag_null" in validate_antares(antares_data)

    def test_untagged_locus_has_no_classification(self, antares_data):
        antares_data["tags"] = []
        assert "no_classification" in validate_antares(antares_data)

    def test_fetch_errors_become_issue_tokens(self, antares_data):
        antares_data["fetch_errors"] = ["locus:timed out", "tags:boom"]
        issues = validate_antares(antares_data)
        assert "fetch_error_locus" in issues and "fetch_error_tags" in issues


class TestClassifyAntares:
    def test_single_science_tag_passes(self):
        cl = classify_antares(["nuclear_transient"])
        assert cl["verdict"] == "pass"
        assert cl["top_class"] == "nuclear_transient"
        assert cl["consensus"] == 1.0
        assert cl["n_classifiers"] == 1 and cl["flag"] is None

    def test_pipeline_tags_do_not_count_toward_consensus(self):
        """One science tag plus three pipeline tags is still an unambiguous locus."""
        cl = classify_antares([
            "nuclear_transient", "lc_feature_extractor", "high_snr", "in_LSSTDDF",
        ])
        assert cl["verdict"] == "pass"
        assert cl["n_classifiers"] == 1
        assert cl["top_class"] == "nuclear_transient"

    def test_two_science_tags_split_the_vote(self):
        cl = classify_antares(["extragalactic", "dimmers"])
        assert cl["consensus"] == 0.5
        assert cl["verdict"] == "review_major"
        assert cl["n_agree"] == 1 and cl["n_disagree"] == 1

    def test_top_class_is_sorted_and_joined(self):
        cl = classify_antares(["nuclear_transient", "dimmers", "extragalactic"])
        assert cl["top_class"] == "dimmers, extragalactic, nuclear_transient"
        assert cl["n_classifiers"] == 3

    def test_review_minor_is_unreachable(self):
        """
        consensus is 1/n, so two tags give 0.50 — under MAJORITY_THRESHOLD (0.65).
        Nothing between "one tag" and "ambiguous" exists in this scheme. Asserted
        rather than left implicit so a threshold change surfaces here.
        """
        for n in range(2, 8):
            cl = classify_antares(sorted(
                ["dimmers", "extragalactic", "nuclear_transient", "blue_transient",
                 "in_m31", "nova_test", "sso_confirmed"]
            )[:n])
            assert cl["verdict"] == "review_major"

    def test_pipeline_only_tags_name_what_was_present(self):
        cl = classify_antares(["lc_feature_extractor", "high_snr"])
        assert cl["verdict"] == "review_major"
        assert cl["n_classifiers"] == 0
        assert "no_science_tags" in cl["flag"]
        assert "lc_feature_extractor" in cl["flag"] and "high_snr" in cl["flag"]

    def test_unknown_tags_are_reported_not_silently_ignored(self):
        """A new ANTARES tag must be visible, not absorbed into 'no classification'."""
        cl = classify_antares(["brand_new_filter_2027"])
        assert "unknown: ['brand_new_filter_2027']" in cl["flag"]
        assert cl["n_classifiers"] == 0

    def test_no_tags_at_all(self):
        cl = classify_antares([])
        assert cl["flag"] == "no_classification"
        assert cl["top_class"] is None and cl["consensus"] is None


class TestBuildAntaresQaRow:
    def test_clean_locus_passes(self, antares_data):
        cl = classify_antares(antares_data["tags"])
        row = build_antares_qa_row("ANT1", antares_data, [], cl)
        assert row["status"] == "PASS"
        assert row["oid"] == "ANT1" and row["ndet"] == 3
        assert row["confirmed"] is True
        assert row["flag"] is None

    def test_schema_matches_alerce_output_columns(self, antares_data):
        cl = classify_antares(antares_data["tags"])
        row = build_antares_qa_row("ANT1", antares_data, [], cl)
        assert list(row) == OUTPUT_COLUMNS

    def test_mag_range_and_timespan_derived(self, antares_data):
        cl = classify_antares(antares_data["tags"])
        row = build_antares_qa_row("ANT1", antares_data, [], cl)
        assert row["mag_range"] == pytest.approx(0.8)     # 18.9 − 18.1
        assert row["timespan_days"] == pytest.approx(12.0)  # 60012 − 60000

    def test_completeness_issues_force_flag_status(self, antares_data):
        cl = classify_antares(antares_data["tags"])
        row = build_antares_qa_row("ANT1", antares_data, ["ndet_lt_2"], cl)
        assert row["status"] == "FLAG"
        assert row["has_issues"] is True
        assert "completeness: ndet_lt_2" in row["flag"]

    def test_multiple_science_tags_yield_review_major(self, antares_data):
        antares_data["tags"] = ["extragalactic", "dimmers"]
        cl = classify_antares(antares_data["tags"])
        row = build_antares_qa_row("ANT1", antares_data, [], cl)
        assert row["status"] == "REVIEW_MAJOR"
        assert row["n_classifiers"] == 2

    def test_single_science_tag_is_not_penalised_for_being_alone(self, antares_data):
        """
        The ALeRCE row flags n_classifiers < 2 as insufficient_classifiers. ANTARES
        tags are not classifier votes, so one tag is a complete answer — this is the
        one place the two row builders must disagree.
        """
        cl = classify_antares(["nuclear_transient"])
        row = build_antares_qa_row("ANT1", antares_data, [], cl)
        assert row["n_classifiers"] == 1
        assert row["status"] == "PASS"

    def test_empty_locus_degrades_without_raising(self, antares_data_empty):
        cl = classify_antares([])
        issues = validate_antares(antares_data_empty)
        row = build_antares_qa_row("ANT1", antares_data_empty, issues, cl)
        assert row["status"] == "FLAG"
        assert row["ndet"] == 0 and row["confirmed"] is False
        assert row["mag_range"] != row["mag_range"]  # NaN


class TestRunAntaresPipeline:
    def _locus_data(self, tags):
        return {
            "dets": pd.DataFrame({
                "ra": [1.0, 1.0], "dec": [2.0, 2.0],
                "ant_mag": [19.0, 18.5], "mjd": [60000.0, 60010.0],
            }),
            "ms": pd.DataFrame([{"ndet": 2, "magmin": 18.5, "magmax": 19.0}]),
            "tags": tags,
            "fetch_errors": [],
        }

    def test_one_row_per_locus_with_full_schema(self):
        from rubin_qa import antares_client

        with patch.object(
            antares_client, "fetch_antares_locus",
            lambda lid: self._locus_data(["nuclear_transient"]),
        ):
            df = run_antares_pipeline(
                locus_ids=["ANT1", "ANT2", "ANT3"],
                inter_object_delay=0, quiet=True,
            )
        assert len(df) == 3
        assert list(df.columns) == OUTPUT_COLUMNS
        assert list(df["oid"]) == ["ANT1", "ANT2", "ANT3"]
        assert set(df["status"]) == {"PASS"}

    def test_explicit_ids_skip_the_candidate_fetch(self):
        from rubin_qa import antares_client

        fetch = MagicMock()
        with patch.object(antares_client, "fetch_antares_candidates", fetch), \
             patch.object(
                 antares_client, "fetch_antares_locus",
                 lambda lid: self._locus_data(["dimmers"]),
             ):
            run_antares_pipeline(locus_ids=["ANT1"], inter_object_delay=0, quiet=True)
        fetch.assert_not_called()

    def test_candidate_fetch_used_when_no_ids_given(self):
        from rubin_qa import antares_client

        with patch.object(
            antares_client, "fetch_antares_candidates",
            MagicMock(return_value=["ANT7", "ANT8"]),
        ), patch.object(
            antares_client, "fetch_antares_locus",
            lambda lid: self._locus_data(["extragalactic"]),
        ):
            df = run_antares_pipeline(page_size=2, inter_object_delay=0, quiet=True)
        assert list(df["oid"]) == ["ANT7", "ANT8"]

    def test_empty_candidate_list_returns_typed_empty_frame(self):
        """
        A failed candidate fetch must still return the report schema — __main__
        writes no CSV and exits 1 on an empty frame, and a bare DataFrame() would
        break the column check on the way there.
        """
        from rubin_qa import antares_client

        with patch.object(
            antares_client, "fetch_antares_candidates", MagicMock(return_value=[])
        ):
            df = run_antares_pipeline(page_size=10, inter_object_delay=0, quiet=True)
        assert df.empty
        assert list(df.columns) == OUTPUT_COLUMNS

    def test_one_bad_locus_does_not_abort_the_run(self):
        """A 500 on one locus costs one FLAG row, not the whole daily report."""
        from rubin_qa import antares_client

        def flaky(lid):
            if lid == "ANT2":
                return {
                    "dets": pd.DataFrame(), "ms": pd.DataFrame(), "tags": [],
                    "fetch_errors": ["locus:500 server error"],
                }
            return self._locus_data(["nuclear_transient"])

        with patch.object(antares_client, "fetch_antares_locus", flaky):
            df = run_antares_pipeline(
                locus_ids=["ANT1", "ANT2", "ANT3"],
                inter_object_delay=0, quiet=True,
            )
        assert len(df) == 3
        assert list(df["status"]) == ["PASS", "FLAG", "PASS"]
        assert "fetch_error_locus" in df.iloc[1]["completeness_issues"]

    def test_inter_object_delay_paces_the_loop(self):
        """Rate-limit pacing is per locus, and 0 must mean no sleep at all."""
        from rubin_qa import antares_client

        with patch.object(
            antares_client, "fetch_antares_locus",
            lambda lid: self._locus_data(["nuclear_transient"]),
        ), patch("rubin_qa.reporting.time.sleep") as mock_sleep:
            run_antares_pipeline(
                locus_ids=["ANT1", "ANT2", "ANT3"], inter_object_delay=0.5, quiet=True,
            )
            assert [c.args[0] for c in mock_sleep.call_args_list] == [0.5, 0.5, 0.5]
            mock_sleep.reset_mock()
            run_antares_pipeline(
                locus_ids=["ANT1"], inter_object_delay=0, quiet=True,
            )
            mock_sleep.assert_not_called()

    def test_progress_output_suppressed_by_quiet(self, capsys):
        from rubin_qa import antares_client

        with patch.object(
            antares_client, "fetch_antares_locus",
            lambda lid: self._locus_data(["nuclear_transient"]),
        ):
            run_antares_pipeline(locus_ids=["ANT1"], inter_object_delay=0, quiet=True)
            assert capsys.readouterr().out == ""
            run_antares_pipeline(locus_ids=["ANT1"], inter_object_delay=0, quiet=False)
            assert "ANT1" in capsys.readouterr().out
