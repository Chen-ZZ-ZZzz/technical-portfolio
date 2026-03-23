"""Tests for log_scan.py."""

import json
import pytest
from log_scan import (
    _build_level_pattern, _extract_timestamp, _format_csv, _format_json,
    scan_dir, scan_file,
)


@pytest.fixture
def sample_log(tmp_path):
    f = tmp_path / "app.log"
    f.write_text(
        "2026-03-21 14:05:03 INFO  app started\n"
        "2026-03-21 14:05:05 ERROR database timeout\n"
        "2026-03-21 14:05:06 WARN  retrying\n"
        "2026-03-21 14:06:00 INFO  connected\n"
        "21.03.2026 14:10:33 ERROR disk failure\n"
    )
    return f


@pytest.fixture
def default_pattern():
    return _build_level_pattern(("ERROR", "WARN"))


class TestExtractTimestamp:
    @pytest.mark.parametrize("line,expected", [
        ("2026-03-21 14:05:03 ERROR x",                    "2026-03-21 14:05:03"),
        ("2026-03-21T14:05:03 ERROR x",                    "2026-03-21T14:05:03"),
        ("2026-03-21T14:05:03.123456+01:00 host ERROR x",  "2026-03-21T14:05:03.123456+01:00"),
        ("21.03.2026 14:05:03 ERROR x",                    "21.03.2026 14:05:03"),
        ("Mar 21 14:05:03 ERROR x",                        "Mar 21 14:05:03"),
        ("ERROR bare line",                                 None),
    ])
    def test_formats(self, line, expected):
        assert _extract_timestamp(line) == expected


class TestBuildLevelPattern:
    def test_matches_levels(self):
        pat = _build_level_pattern(("ERROR", "WARN"))
        assert pat.search("an ERROR occurred")
        assert not pat.search("ERRORCODE")  # word boundary


class TestScanFile:
    def test_counts_and_line_numbers(self, sample_log, default_pattern):
        report = scan_file(sample_log, default_pattern)
        assert report.counts["ERROR"] == 2
        assert report.counts["WARN"] == 1
        assert [h.line_number for h in report.hits] == [2, 3, 5]


class TestScanDir:
    def test_finds_log_files_only(self, tmp_path, default_pattern):
        logs = tmp_path / "logs"
        logs.mkdir()
        (logs / "app.log").write_text("ERROR one\n")
        (logs / "readme.txt").write_text("ERROR ignored\n")
        reports = scan_dir(logs, default_pattern)
        assert len(reports) == 1
        assert reports[0].path.name == "app.log"


class TestFormatters:
    def test_json_valid(self, sample_log, default_pattern):
        report = scan_file(sample_log, default_pattern)
        data = json.loads(_format_json([report]))
        assert data["files"][0]["counts"]["ERROR"] == 2
        assert len(data["files"][0]["hits"]) == 3

    def test_csv_header_and_rows(self, sample_log, default_pattern):
        report = scan_file(sample_log, default_pattern)
        lines = _format_csv([report]).strip().splitlines()
        assert lines[0] == "file,line_number,level,timestamp,line"
        assert len(lines) == 4  # header + 3 hits
