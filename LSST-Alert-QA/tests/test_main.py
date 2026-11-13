"""
Tests for the CLI entry point: exit codes and report writing.

Offline — the pipelines are stubbed, so nothing here touches a broker. The
contract under test is what an operator and systemd actually observe: which runs
write a CSV, which exit non-zero, and which do both or neither.
"""

import sys
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import rubin_qa.__main__ as main_module
from rubin_qa.config import OUTPUT_COLUMNS, mjd_to_datetime, utc_and_age
from tests import fake_antares

# 2026-11-04 06:49:52.5 UTC, the 11-04 ANTARES unit's start; the half second keeps the
# whole-second stamp off a float boundary
RUN_MJD = 61348 + (6 * 3600 + 49 * 60 + 52.5) / 86400


def _report(n: int = 3, flagged: int = 1, newest_ztf: float | None = 61347.5572) -> pd.DataFrame:
    """A QA report shaped like the real thing, with `flagged` rows carrying a flag."""
    rows = []
    for i in range(n):
        row = dict.fromkeys(OUTPUT_COLUMNS)
        row.update(
            oid=f"ZTF{i}",
            ndet=5,
            newest_ztf_mjd=newest_ztf,
            unknown_survey_dets=0,
            top_class="SN",
            consensus=1.0,
            n_classifiers=2,
            status="PASS",
            flag="completeness: mag_null" if i < flagged else None,
            flag_reasons=["mag_null"] if i < flagged else [],
        )
        rows.append(row)
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)


def _run_cli(argv, tmp_path, result=None, side_effect=None, clock=None):
    """
    Invoke main() with both pipelines stubbed and reports redirected to tmp_path. clock:
    a dict {"mjd": ...} the stubbed now_mjd reads (default: a fixed RUN_MJD).
    """
    clock = clock if clock is not None else {"mjd": RUN_MJD}
    pipeline = MagicMock(return_value=result, side_effect=side_effect)
    with patch.object(sys, "argv", ["pipeline.py", *argv]), \
         patch.object(main_module, "REPORTS_DIR", tmp_path), \
         patch.object(main_module, "now_mjd", lambda: clock["mjd"]), \
         patch.object(main_module, "run_pipeline", pipeline), \
         patch.object(main_module, "run_antares_pipeline", pipeline):
        main_module.main()
    return pipeline


def _csvs(tmp_path):
    return sorted(tmp_path.glob("*.csv"))


class TestExitCodes:
    def test_successful_run_exits_zero_and_writes_csv(self, tmp_path):
        _run_cli(["ztf", "3"], tmp_path, result=_report(3))  # no SystemExit
        assert len(_csvs(tmp_path)) == 1

    def test_empty_report_exits_one_and_writes_nothing(self, tmp_path, capsys):
        # A header-only CSV would look like a successful run in the reports dir.
        with pytest.raises(SystemExit) as exc:
            _run_cli(["ztf", "3"], tmp_path, result=pd.DataFrame(columns=OUTPUT_COLUMNS))
        assert exc.value.code == 1
        assert _csvs(tmp_path) == []
        assert "no objects processed" in capsys.readouterr().err

    def test_pipeline_exception_exits_one_and_writes_nothing(self, tmp_path, capsys):
        with pytest.raises(SystemExit) as exc:
            _run_cli(["ztf", "3"], tmp_path, side_effect=RuntimeError("broker down"))
        assert exc.value.code == 1
        assert _csvs(tmp_path) == []
        err = capsys.readouterr().err
        assert "pipeline failed" in err and "RuntimeError" in err

    def test_partial_report_still_succeeds(self, tmp_path):
        # The deadline path returns fewer rows than requested; that is a usable
        # report, not a failure, so it must write a CSV and exit 0.
        _run_cli(["antares", "50"], tmp_path, result=_report(4))
        written = _csvs(tmp_path)
        assert len(written) == 1
        assert len(pd.read_csv(written[0])) == 4


class TestDeadlineExit:
    def _stopped(self):
        df = _report(3)
        df.attrs["stopped_at_deadline"] = {"done": 3, "of": 196, "elapsed": 2830.0, "deadline": 2822.4}
        return df

    @pytest.mark.parametrize("quiet", [[], ["-q"]])
    def test_a_run_stopped_at_its_deadline_writes_its_csv_and_exits_3(self, tmp_path, capsys, quiet):
        with pytest.raises(SystemExit) as exc:
            _run_cli(["lsst", "196", *quiet], tmp_path, result=self._stopped())
        assert exc.value.code == main_module.EXIT_DEADLINE == 3
        (csv,) = _csvs(tmp_path)
        err = capsys.readouterr().err
        assert err.startswith("ERROR: ALeRCE LSST: stopped at the deadline after 3/196 objects")
        assert f"partial report written: {csv}" in err

    def test_a_full_run_exits_0(self, tmp_path):
        _run_cli(["lsst", "3"], tmp_path, result=_report(3))  # no SystemExit


class TestReportFile:
    def test_filename_records_survey_and_row_count(self, tmp_path):
        _run_cli(["antares", "5"], tmp_path, result=_report(3))
        name = _csvs(tmp_path)[0].name
        assert name.startswith("qa_antares_")
        assert name.endswith("_n3.csv")

    def test_row_count_reflects_rows_not_requested_size(self, tmp_path):
        # ANTARES dedup means the report is routinely smaller than page_size.
        _run_cli(["antares", "256"], tmp_path, result=_report(212))
        assert _csvs(tmp_path)[0].name.endswith("_n212.csv")

    def test_csv_keeps_full_schema(self, tmp_path):
        _run_cli(["ztf", "3"], tmp_path, result=_report(3))
        assert list(pd.read_csv(_csvs(tmp_path)[0]).columns) == ["run_utc", *OUTPUT_COLUMNS]

    @pytest.mark.parametrize("stopped", [False, True])
    def test_the_name_and_run_utc_carry_the_runs_start_not_its_finish(self, tmp_path, stopped):
        """
        2026-11-04: one reading of the clock before the listing; the file name shows it in
        local time, run_utc in UTC. The stubbed pipeline moves the clock on by 10 min, as
        a run would, so a name taken at write time shows the finish. The expected name is
        the reading converted in the test's own zone, so it holds under any TZ.
        """
        clock = {"mjd": RUN_MJD}
        df = _report(3)
        if stopped:
            df.attrs["stopped_at_deadline"] = {"done": 3, "of": 196, "elapsed": 600.0, "deadline": 590.0}

        def run(**kwargs):
            clock["mjd"] += 10 / 1440
            return df

        if stopped:
            with pytest.raises(SystemExit):
                _run_cli(["ztf", "196"], tmp_path, side_effect=run, clock=clock)
        else:
            _run_cli(["ztf", "196"], tmp_path, side_effect=run, clock=clock)
        start = mjd_to_datetime(RUN_MJD)
        (csv,) = _csvs(tmp_path)
        assert csv.name == f"qa_alerce_ztf_{start.astimezone():%Y%m%d_%H%M%S}_n3.csv"
        assert set(pd.read_csv(csv)["run_utc"]) == {start.isoformat(timespec="seconds")}
        assert start.isoformat(timespec="seconds") == "2026-11-04T06:49:52+00:00"

    def test_reports_dir_created_when_absent(self, tmp_path):
        target = tmp_path / "does" / "not" / "exist"
        _run_cli(["ztf", "3"], target, result=_report(2))
        assert len(_csvs(target)) == 1


class TestOutput:
    def test_quiet_prints_one_line_summary(self, tmp_path, capsys):
        _run_cli(["ztf", "3", "-q"], tmp_path, result=_report(3, flagged=2))
        out = capsys.readouterr().out.strip().splitlines()
        assert len(out) == 1
        assert out[0].startswith("ALeRCE ZTF  n=3  flagged=2/3 (mag_null 2)  | newest ZTF of the 3 checked: "
                                 "2026-11-03 13:22 UTC (17.5 h)  → ") and "qa_alerce_ztf_" in out[0]

    def test_an_antares_listing_run_states_its_sample_on_the_one_line(self, tmp_path, capsys):
        df = _report(3)
        df.attrs["sample"] = {"asked": 256, "listed": 183, "checked": 183, "newest_times": 2,
                              "newest_mjd": (61347.4756, 61347.4812), "surveys": {"ZTF": 150, "LSST": 33}}
        _run_cli(["antares", "256", "-q"], tmp_path, result=df)
        out = capsys.readouterr().out.strip().splitlines()
        assert len(out) == 1
        assert out[0].endswith("| sample: 183 loci checked of 183 unique listed (256 asked; the most "
                               "recently updated), newest alerts at 2 distinct times "
                               "(MJD 61347.4756–61347.4812), by survey ZTF 150 / LSST 33")

    def test_a_sample_from_one_exposure_says_so_plainly(self):
        df = _report(1)
        df.attrs["sample"] = {"asked": 60, "listed": 60, "checked": 60, "newest_times": 1,
                              "newest_mjd": (61347.5079, 61347.5079), "surveys": {"ZTF": 59, "unknown": 1}}
        assert main_module.sample_line(df).endswith(
            "newest alerts at 1 distinct time (MJD 61347.5079), by survey ZTF 59 / LSST 0 / unknown 1")

    def test_the_full_report_states_the_sample_with_its_survey_split(self, tmp_path, capsys):
        df = _report(3)
        df.attrs["sample"] = {"asked": 256, "listed": 237, "checked": 237, "newest_times": 3,
                              "newest_mjd": (61347.5562, 61347.5572), "surveys": {"ZTF": 237}}
        _run_cli(["antares", "256"], tmp_path, result=df)
        out = capsys.readouterr().out
        assert "sample: 237 loci checked" in out and "by survey ZTF 237 / LSST 0" in out

    def test_an_antares_run_counts_its_unknown_tags_beside_the_flag_split(self, tmp_path, capsys, monkeypatch):
        """
        Two recorded loci through the pipeline (tests/fake_antares.py), one carrying an unknown
        tag beside a science tag: the count follows the flag split on the one line, and gets
        its own line before it in the full report. ALeRCE runs carry no part (their tests above).
        """
        fake_antares.serve_recorded(monkeypatch)
        df = pd.DataFrame([fake_antares.recorded_row(i) for i in ("ANT2026sx3pbuillwqe", "ANT20262tl85caoepac")],
                          columns=OUTPUT_COLUMNS)
        part = "unknown tags: lantern_xgboost_t2.0.7_c0.95 1"
        _run_cli(["antares", "2", "-q"], tmp_path, result=df.copy())
        (line,) = capsys.readouterr().out.strip().splitlines()
        assert line.startswith(f"ANTARES  n=2  {main_module.flag_split(df)}  | {part}  | newest ZTF of the 2 checked: ")
        _run_cli(["antares", "2"], tmp_path, result=df.copy())
        assert f"\n{part}\n{main_module.flag_split(df)}  |  full report: " in capsys.readouterr().out

    def test_verbose_prints_summary_table_and_flag_count(self, tmp_path, capsys):
        _run_cli(["ztf", "3"], tmp_path, result=_report(3, flagged=2))
        out = capsys.readouterr().out
        assert "=== ALeRCE ZTF Data Quality Report ===" in out
        assert "\nnewest ZTF of the 3 checked: 2026-11-03 13:22 UTC (17.5 h)\nflagged=2/3 (mag_null 2)  |  full report: " in out
        for column in main_module.SUMMARY_COLUMNS:
            assert column in out


class TestNewestLine:
    """
    "newest <SURVEY> of the N checked: <UTC> (<age>)" (2026-11-04): one wording for every
    survey in both pipelines, aged from the run's start, read from the report's columns.
    """

    def test_an_alerce_run_names_its_own_survey_only(self):
        df = _report(4)
        df["newest_ztf_mjd"] = [61340.0, 61347.5572, None, 61346.0]
        assert main_module.newest_line(df, "ztf", RUN_MJD) == (
            "newest ZTF of the 4 checked: 2026-11-03 13:22 UTC (17.5 h)")

    def test_an_antares_run_names_both_surveys_and_says_none_where_none(self):
        df = _report(2)
        assert main_module.newest_line(df, "antares", RUN_MJD) == (
            "newest ZTF of the 2 checked: 2026-11-03 13:22 UTC (17.5 h); newest LSST of the 2 checked: none")

    def test_lsst_is_named_the_same_way_when_it_is_newest(self):
        """With LSST alerting the top loci can be all LSST: no survey is worded as broker-wide."""
        df = _report(2, newest_ztf=None)
        df["newest_lsst_mjd"] = [61344.3188, 61344.0]
        assert main_module.newest_line(df, "antares", RUN_MJD) == (
            "newest ZTF of the 2 checked: none; newest LSST of the 2 checked: 2026-10-31 07:39 UTC (4.0 d)")

    def test_detections_of_no_known_survey_are_counted_never_dropped(self):
        df = _report(3)
        df["unknown_survey_dets"] = [2, 0, 1]
        assert main_module.newest_line(df, "antares", RUN_MJD).endswith(
            "; detections of no known survey: 3 in 2 of the 3 checked")

    def test_the_line_reads_back_from_the_csv(self, tmp_path):
        """The journal rotates; the report alone reproduces its summary."""
        _run_cli(["ztf", "3"], tmp_path, result=_report(3))
        back = pd.read_csv(_csvs(tmp_path)[0])
        assert main_module.newest_line(back, "ztf", RUN_MJD) == main_module.newest_line(_report(3), "ztf", RUN_MJD)

    def test_a_survey_empty_in_every_row_reads_back_as_none(self, tmp_path):
        """An ANTARES run with no LSST detection: the column is empty, NaN on read-back, and the line says none."""
        _run_cli(["antares", "3"], tmp_path, result=_report(3))
        back = pd.read_csv(_csvs(tmp_path)[0])
        assert back["newest_lsst_mjd"].isna().all()
        assert main_module.newest_line(back, "antares", RUN_MJD) == (
            "newest ZTF of the 3 checked: 2026-11-03 13:22 UTC (17.5 h); newest LSST of the 3 checked: none")


class TestFlagSplit:
    def test_the_split_counts_rows_per_key_by_count_then_key(self):
        df = _report(4, flagged=0)
        df["flag"] = ["x", "x", "x", None]
        df["flag_reasons"] = [["insufficient_classifiers", "ndet_lt_2"], ["insufficient_classifiers"],
                              ["insufficient_classifiers", "mag_null", "ndet_lt_2"], []]
        assert main_module.flag_split(df) == "flagged=3/4 (insufficient_classifiers 3, ndet_lt_2 2, mag_null 1)"

    def test_the_split_reads_back_from_the_csv(self, tmp_path):
        """The CSV holds each list as its text; read back, the report gives the same split."""
        _run_cli(["ztf", "3"], tmp_path, result=_report(3, flagged=2))
        back = pd.read_csv(_csvs(tmp_path)[0])
        assert main_module.flag_split(back) == main_module.flag_split(_report(3, flagged=2)) == "flagged=2/3 (mag_null 2)"

    def test_an_empty_flag_reasons_cell_reads_back_as_no_key(self, tmp_path):
        """The writer leaves no cell empty (an unflagged row writes []); one that is reads back as NaN: no key."""
        df = _report(3, flagged=2)
        df["flag_reasons"] = [["mag_null"], ["mag_null"], None]
        _run_cli(["ztf", "3"], tmp_path, result=df)
        back = pd.read_csv(_csvs(tmp_path)[0])
        assert back["flag_reasons"].isna().sum() == 1
        assert main_module.flag_split(back) == "flagged=2/3 (mag_null 2)"

    def test_nothing_flagged_has_no_split(self):
        assert main_module.flag_split(_report(3, flagged=0)) == "flagged=0/3"


class TestDispatch:
    def test_numeric_target_is_a_page_size(self, tmp_path):
        pipeline = _run_cli(["ztf", "25"], tmp_path, result=_report(2))
        assert pipeline.call_args.kwargs["page_size"] == 25
        assert "oids" not in pipeline.call_args.kwargs

    def test_non_numeric_targets_are_object_ids(self, tmp_path):
        pipeline = _run_cli(["ztf", "ZTF1", "ZTF2"], tmp_path, result=_report(2))
        assert pipeline.call_args.kwargs["oids"] == ["ZTF1", "ZTF2"]
        assert "page_size" not in pipeline.call_args.kwargs

    def test_antares_page_size_reaches_the_pipeline(self, tmp_path):
        # 2026-10-04: a unit edited to "antares 25" still listed ~256 loci; the number
        # on the command line is what the pipeline gets, nothing in between overrides it
        pipeline = _run_cli(["antares", "25"], tmp_path, result=_report(3))
        assert pipeline.call_args.kwargs["page_size"] == 25

    def test_antares_targets_route_to_locus_ids(self, tmp_path):
        pipeline = _run_cli(["antares", "ANT1", "ZTF20aafqubg"], tmp_path, result=_report(2))
        assert pipeline.call_args.kwargs["locus_ids"] == ["ANT1", "ZTF20aafqubg"]

    def test_survey_defaults_to_ztf(self, tmp_path):
        pipeline = _run_cli([], tmp_path, result=_report(2))
        assert pipeline.call_args.kwargs["survey"] == "ztf"

    def test_quiet_is_forwarded(self, tmp_path):
        pipeline = _run_cli(["ztf", "3", "-q"], tmp_path, result=_report(2))
        assert pipeline.call_args.kwargs["quiet"] is True


class TestSurveyValidation:
    """
    The survey positional used to accept any string, so a bare page size was read
    as a survey name: `pipeline.py 50` ran ztf's default 100-object page against
    a broker called "50" and wrote qa_50_*.csv. argparse rejects it now.
    """

    def test_bare_page_size_is_rejected(self, tmp_path, capsys):
        with pytest.raises(SystemExit) as exc:
            _run_cli(["50"], tmp_path, result=_report(3))
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "invalid choice: '50'" in err
        assert "ztf" in err and "antares" in err
        assert _csvs(tmp_path) == []

    def test_unknown_survey_is_rejected(self, tmp_path, capsys):
        with pytest.raises(SystemExit) as exc:
            _run_cli(["ztfff", "10"], tmp_path, result=_report(3))
        assert exc.value.code == 2
        assert "invalid choice: 'ztfff'" in capsys.readouterr().err
        assert _csvs(tmp_path) == []

    def test_bare_oid_without_a_survey_is_rejected(self, tmp_path):
        """`pipeline.py ZTF123` skipped the survey, so the oid became the survey."""
        with pytest.raises(SystemExit) as exc:
            _run_cli(["ZTF21abcdefg"], tmp_path, result=_report(1))
        assert exc.value.code == 2
        assert _csvs(tmp_path) == []

    @pytest.mark.parametrize("survey,label,slug", [
        ("ztf", "ALeRCE ZTF", "alerce_ztf"), ("lsst", "ALeRCE LSST", "alerce_lsst"), ("antares", "ANTARES", "antares"),
    ])
    def test_every_listed_survey_still_dispatches(self, survey, label, slug, tmp_path, capsys):
        """One name per run, the same in the header, the report heading and the file (2026-10-04)."""
        pipeline = _run_cli([survey, "5"], tmp_path, result=_report(2))
        assert pipeline.called
        assert _csvs(tmp_path)[0].name.startswith(f"qa_{slug}_")
        out = capsys.readouterr().out
        assert f"=== {label} Alert Data Quality Pipeline  page_size=5 ===" in out
        assert f"=== {label} Data Quality Report ===" in out

    def test_choices_match_the_config_registry(self):
        """The help text and the accepted set must not drift apart."""
        from rubin_qa.config import DEFAULT_SURVEY, SURVEYS

        assert DEFAULT_SURVEY in SURVEYS
