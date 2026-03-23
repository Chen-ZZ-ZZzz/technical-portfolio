"""Tests for common.py."""

import re
import pytest
from common import file_timestamp, report_timestamp, save_report


class TestTimestamps:
    def test_report_timestamp_format(self):
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", report_timestamp())

    def test_file_timestamp_format(self):
        assert re.fullmatch(r"\d{8}_\d{6}", file_timestamp())


class TestSaveReport:
    def test_creates_json_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("common.REPORTS_DIR", tmp_path / "reports")
        path = save_report('{"test": true}', "json", "net_check")
        assert path.exists()
        assert path.suffix == ".json"
        assert path.read_text() == '{"test": true}'

    def test_returns_none_on_permission_error(self, tmp_path, monkeypatch):
        blocked = tmp_path / "nope"
        blocked.mkdir(mode=0o000)
        monkeypatch.setattr("common.REPORTS_DIR", blocked / "reports")
        assert save_report("content", "json", "test") is None
        blocked.chmod(0o755)
