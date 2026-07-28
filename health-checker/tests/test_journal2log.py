import pytest

from journal2log import _prio_conv, _msg_conv, _own_out, _rt_conv


@pytest.mark.parametrize("level,expected", [
    ("0",     "ERROR"),
    ("3",     "ERROR"),
    ("4",     "WARN"),
    ("6",     None),
    ("9527",  None),
    ("junk",  None),
    (None,    None),            # field absent
])
def test_priorities(level, expected):
    assert _prio_conv(level) == expected


@pytest.mark.parametrize("message,expected", [
    ("Finished lunch.service",       "Finished lunch.service"),
    ([112, 114, 111, 98, 101, 255],  "probe\ufffd"  ),
    ([300],                          None),  # ValueError
    ("",                             None),
])
def test_messages(message, expected):
    assert _msg_conv(message) == expected


@pytest.mark.parametrize("unit,expected", [
    ("journal_scan.service",  True),
    ("sso-monitor.service",   False),
    (None,                    False),
])
def test_own_unit_exclusion(unit, expected):
    assert _own_out(unit) == expected


def test_timestamps():
    assert _rt_conv("1784442336296183") == "2026-07-19 08:25:36.296183"
