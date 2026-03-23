"""Tests for net_check.py."""

import json
from unittest.mock import MagicMock, patch

import pytest
from net_check import (
    HostResult, PortResult,
    _format_csv, _format_json, _parse_latency,
    check_port, load_hosts, ping,
)


class TestHostResult:
    def test_passed_when_ping_ok(self):
        assert HostResult(host="x", ping_passed=True).passed is True

    def test_failed_when_ping_fails(self):
        assert HostResult(host="x", ping_passed=False).passed is False

    def test_passed_ignores_closed_ports(self):
        r = HostResult(host="x", ping_passed=True,
                       port_results=[PortResult(22, False)])
        assert r.passed is True


class TestParseLatency:
    def test_extracts_avg(self):
        output = "rtt min/avg/max/mdev = 10.0/12.3/15.0/1.2 ms\n"
        assert _parse_latency(output) == pytest.approx(12.3)

    def test_returns_none_on_no_match(self):
        assert _parse_latency("no rtt line here") is None


class TestLoadHosts:
    def test_from_file(self, tmp_path):
        f = tmp_path / "hosts.txt"
        f.write_text("google.com\n# comment\n192.168.1.1\n\n")
        assert load_hosts(str(f)) == ["google.com", "192.168.1.1"]

    def test_from_comma_separated(self):
        assert load_hosts("a.com,b.com") == ["a.com", "b.com"]


class TestCheckPort:
    def test_closed_port(self):
        with patch("net_check.socket.create_connection", side_effect=OSError):
            result = check_port("localhost", 9999, timeout=1)
        assert result.open is False


class TestPing:
    def test_success(self):
        proc = MagicMock(returncode=0,
                         stdout="rtt min/avg/max/mdev = 10.0/12.3/15.0/1.2 ms\n")
        with patch("net_check.subprocess.run", return_value=proc):
            ok, latency = ping("1.1.1.1")
        assert ok is True
        assert latency == pytest.approx(12.3)


class TestFormatters:
    def test_json_valid(self):
        r = HostResult(host="1.1.1.1", ping_passed=True, ping_latency_ms=12.3)
        data = json.loads(_format_json([r]))
        assert data["results"][0]["host"] == "1.1.1.1"
        assert data["results"][0]["passed"] is True

    def test_csv_has_port_columns(self):
        r = HostResult(host="x", ping_passed=True,
                       port_results=[PortResult(443, True, 15.0)])
        header = _format_csv([r], [443]).splitlines()[0]
        assert "port_443" in header
