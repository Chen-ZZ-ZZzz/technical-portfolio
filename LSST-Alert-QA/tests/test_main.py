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
from rubin_qa.config import OUTPUT_COLUMNS


def _report(n: int = 3, flagged: int = 1) -> pd.DataFrame:
    """A QA report shaped like the real thing, with `flagged` rows carrying a flag."""
    rows = []
    for i in range(n):
        row = dict.fromkeys(OUTPUT_COLUMNS)
        row.update(
            oid=f"ZTF{i}",
            ndet=5,
            top_class="SN",
            consensus=1.0,
            n_classifiers=2,
            status="PASS",
            flag="completeness: mag_null" if i < flagged else None,
        )
        rows.append(row)
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)


def _run_cli(argv, tmp_path, result=None, side_effect=None):
    """Invoke main() with both pipelines stubbed and reports redirected to tmp_path."""
    pipeline = MagicMock(return_value=result, side_effect=side_effect)
    with patch.object(sys, "argv", ["pipeline.py", *argv]), \
         patch.object(main_module, "REPORTS_DIR", tmp_path), \
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
        assert list(pd.read_csv(_csvs(tmp_path)[0]).columns) == OUTPUT_COLUMNS

    def test_reports_dir_created_when_absent(self, tmp_path):
        target = tmp_path / "does" / "not" / "exist"
        _run_cli(["ztf", "3"], target, result=_report(2))
        assert len(_csvs(target)) == 1


class TestOutput:
    def test_quiet_prints_one_line_summary(self, tmp_path, capsys):
        _run_cli(["ztf", "3", "-q"], tmp_path, result=_report(3, flagged=2))
        out = capsys.readouterr().out.strip().splitlines()
        assert len(out) == 1
        assert "ztf" in out[0] and "n=3" in out[0] and "flagged=2/3" in out[0]

    def test_verbose_prints_summary_table_and_flag_count(self, tmp_path, capsys):
        _run_cli(["ztf", "3"], tmp_path, result=_report(3, flagged=2))
        out = capsys.readouterr().out
        assert "=== QA Report ===" in out
        assert "2/3 objects flagged" in out
        for column in main_module.SUMMARY_COLUMNS:
            assert column in out


class TestDispatch:
    def test_numeric_target_is_a_page_size(self, tmp_path):
        pipeline = _run_cli(["ztf", "25"], tmp_path, result=_report(2))
        assert pipeline.call_args.kwargs["page_size"] == 25
        assert "oids" not in pipeline.call_args.kwargs

    def test_non_numeric_targets_are_object_ids(self, tmp_path):
        pipeline = _run_cli(["ztf", "ZTF1", "ZTF2"], tmp_path, result=_report(2))
        assert pipeline.call_args.kwargs["oids"] == ["ZTF1", "ZTF2"]
        assert "page_size" not in pipeline.call_args.kwargs

    def test_antares_targets_route_to_locus_ids(self, tmp_path):
        pipeline = _run_cli(["antares", "ANT1", "ZTF20aafqubg"], tmp_path, result=_report(2))
        assert pipeline.call_args.kwargs["locus_ids"] == ["ANT1", "ZTF20aafqubg"]

    def test_survey_defaults_to_ztf(self, tmp_path):
        pipeline = _run_cli([], tmp_path, result=_report(2))
        assert pipeline.call_args.kwargs["survey"] == "ztf"

    def test_quiet_is_forwarded(self, tmp_path):
        pipeline = _run_cli(["ztf", "3", "-q"], tmp_path, result=_report(2))
        assert pipeline.call_args.kwargs["quiet"] is True
