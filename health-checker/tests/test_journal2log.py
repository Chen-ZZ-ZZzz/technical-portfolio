import pytest
from journal2log import _prio_conv, _msg_conv, _own_out, _rt_conv, convert_record, main

_REAL_RECORD = '{"_SYSTEMD_UNIT":"session-c47.scope","PRIORITY":"3","__REALTIME_TIMESTAMP":"1787895664352627","MESSAGE":"pam_systemd(lightdm-greeter:session): Failed to release session: Transport endpoint is not connected","_TRANSPORT":"syslog","_COMM":"lightdm","SYSLOG_IDENTIFIER":"lightdm","_PID":"181820"}'


class TestHelpers:
    @pytest.mark.parametrize(
        "level,expected",
        [
            ("0", "ERROR"),
            ("3", "ERROR"),
            ("4", "WARN"),
            ("6", None),
            ("9527", None),
            ("junk", None),
            (None, None),  # field absent
        ],
    )
    def test_priorities(self, level, expected):
        assert _prio_conv(level) == expected

    @pytest.mark.parametrize(
        "message,expected",
        [
            ("Finished lunch.service", "Finished lunch.service"),
            ([112, 114, 111, 98, 101, 255], "probe\ufffd"),
            ([300], None),  # ValueError
            ("", None),
        ],
    )
    def test_messages(self, message, expected):
        assert _msg_conv(message) == expected

    @pytest.mark.parametrize(
        "unit,expected",
        [
            ("checker-journal-scan.service", True),
            ("sso-monitor.service", False),
            (None, False),
        ],
    )
    def test_own_unit_exclusion(self, unit, expected):
        assert _own_out(unit) == expected

    def test_timestamps(self):
        assert _rt_conv("1784442336296183") == "2026-07-19 08:25:36.296183"


class TestConvert:
    def test_convert_real_record(self):
        out = convert_record(_REAL_RECORD)
        assert out == (
            "2026-08-28 07:41:04.352627 ERROR: "
            "lightdm[181820]: "
            "pam_systemd(lightdm-greeter:session): Failed to release "
            "session: Transport endpoint is not connected"
        )

    @pytest.mark.parametrize(
        "line",
        [
            "",                 # empty
            "{broken",          # JSONDecodeError
            '{"PRIORITY":"6","MESSAGE":"x","__REALTIME_TIMESTAMP":"1787895664352627"}',  # info
            '{"PRIORITY":"3","MESSAGE":"x","__REALTIME_TIMESTAMP":"1787895664352627",'
            '"_SYSTEMD_USER_UNIT":"checker-journal-scan.service"}',                      # own output
            '{"PRIORITY":"3","__REALTIME_TIMESTAMP":"1787895664352627"}',                # no MESSAGE
        ],
    )
    def test_convert_drops(self, line):
        assert convert_record(line) is None


class TestMain:
    def test_main_roundtrip(self, tmp_path):
        infile = tmp_path / "in.json"
        outfile = tmp_path / "out.log"
        infile.write_text(
            _REAL_RECORD
            + "\n"
            + '{"PRIORITY":"6","MESSAGE":"noise","__REALTIME_TIMESTAMP":"1787895664352627"}\n'
        )
        main([str(infile), str(outfile)])

        lines = outfile.read_text().splitlines()
        assert len(lines) == 1  # info record dropped
        assert lines[0].endswith("not connected")

    def test_main_empty(self, tmp_path):
        infile = tmp_path / "in.json"
        outfile = tmp_path / "out.log"
        infile.write_text(
            '{"PRIORITY":"6","MESSAGE":"noise","__REALTIME_TIMESTAMP":"1787895664352627"}\n'
        )
        main([str(infile), str(outfile)])

        assert outfile.exists()
        assert outfile.read_text() == ""
