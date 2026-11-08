"""
rubin_qa/tns.py - TNS lookups through ALeRCE's TNS service.

The service answers HTTP 200 whatever happened, and the body says which. These tests serve
its recorded bodies verbatim (tests/fake_tns.py), on a fake clock; no network. Moved here
from test_host_match_check.py on 2026-10-02 with the code (the monitor's point-source watch
uses the lookup too).
"""

from types import SimpleNamespace

import pytest
import requests

from rubin_qa import tns as t
from tests import fake_tns as fake


def test_tns_names_are_decoded_where_they_come_in(monkeypatch):
    """2026fgl's host arrived as 2MASXJ09565234%2B0328119; a literal + must survive (unquote, not unquote_plus)."""
    record = {"objname": "2026fgl", "hostname": "2MASXJ09565234%2B0328119, SDSS J095652.34+032811.9",
              "internal_names": "ZTF26aalbzqr,LSST-AP-DO-1706", "reporter": "A. B%2BC", "redshift": 0.164}
    response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"object_data": record})
    monkeypatch.setattr(t.requests, "post", lambda url, json, timeout: response)
    rec = t.tns_once(149.218, 3.470)
    assert rec["hostname"] == "2MASXJ09565234+0328119, SDSS J095652.34+032811.9"
    assert rec["internal_names"] == "ZTF26aalbzqr,LSST-AP-DO-1706" and rec["redshift"] == 0.164
    assert rec["reporter"] == "A. B%2BC"  # not a name: left as sent


@pytest.fixture
def clock(monkeypatch):
    return fake.fake_clock(monkeypatch)


# --- one call: what a body says ---------------------------------------------------------------


def test_a_record_body_gives_the_record(monkeypatch):
    fake.serve(monkeypatch, fake.RECORD)
    rec = t.tns_once(52.09, -27.38)
    assert rec["objname"] == "2026afma" and rec["discoverydate"] == "2026-09-28 08:14:49.632"


def test_the_none_body_gives_empty_and_nothing_else_does(monkeypatch):
    fake.serve(monkeypatch, fake.NONE)
    assert t.tns_once(10.0, -45.0) == {}


@pytest.mark.parametrize("body", [fake.REJECTED, fake.UPSTREAM_ERROR], ids=["rejected", "upstream_error"])
def test_a_rejected_call_or_an_upstream_failure_raises_never_reads_as_none(monkeypatch, capsys, body):
    fake.serve(monkeypatch, body)
    with pytest.raises(t.TnsUnavailable) as raised:
        t.tns_once(52.09, -27.38)
    assert not isinstance(raised.value, t.TnsUnknownBody)  # a shape we know: nothing to warn about
    assert capsys.readouterr().err == ""


UNKNOWN_SHAPES = {
    "other_data": '{"data":"x","id_code":200,"id_message":"OK"}',
    "other_code": '{"data":[],"id_code":201,"id_message":"OK"}',
    "server_code": '{"id_code":500}',
    "empty_object": "{}",
    "empty_object_data": '{"object_data":{},"object_name":"x"}',
    "list": "[]",
    "null": "null",
    "not_json": "<html>Bad Gateway</html>",
    "empty": "",
}


@pytest.mark.parametrize("body", UNKNOWN_SHAPES.values(), ids=UNKNOWN_SHAPES.keys())
def test_a_body_of_no_known_shape_is_unavailable_at_once_and_shows_in_the_journal(clock, monkeypatch, capsys, body):
    asked = fake.serve(monkeypatch, body)
    assert t.tns_lookup(52.09, -27.38) is None
    assert len(asked) == 1  # it may not be temporary: not asked again
    assert f"WARN: TNS answered with a body of no known shape: {body!r}\n" == capsys.readouterr().err


def test_the_journal_gets_only_the_first_bytes_of_an_unknown_body(clock, monkeypatch, capsys):
    fake.serve(monkeypatch, "x" * 5000)
    assert t.tns_lookup(52.09, -27.38) is None
    assert capsys.readouterr().err == f"WARN: TNS answered with a body of no known shape: {'x' * 200!r}\n"


# --- a lookup: asked again while it is temporary ------------------------------------------------


def test_a_none_takes_one_ask(clock, monkeypatch):
    asked = fake.serve(monkeypatch, fake.NONE)
    assert t.tns_lookup(10.0, -45.0) == {}
    assert len(asked) == 1 and clock.slept == [6.5]


@pytest.mark.parametrize("dropped", [fake.REJECTED, fake.UPSTREAM_ERROR, requests.ConnectionError("down"), requests.Timeout("hung")],
                         ids=["rejected", "upstream_error", "failed_request", "timed_out"])
def test_a_dropped_call_then_a_record_is_the_record_never_none(clock, monkeypatch, dropped):
    """The question that ended the canary: an empty answer that may be a dropped call must not become "none"."""
    asked = fake.serve(monkeypatch, dropped, fake.RECORD)
    assert t.tns_lookup(52.09, -27.38)["objname"] == "2026afma"
    assert len(asked) == 2 and clock.slept == [6.5, 15.0, 6.5]


def test_a_failed_http_status_is_a_failed_request_and_is_asked_again(clock, monkeypatch):
    def refused():
        raise requests.HTTPError("502")

    answers = [SimpleNamespace(raise_for_status=refused), fake.response(fake.RECORD)]
    monkeypatch.setattr(t.requests, "post", lambda url, json, timeout: answers.pop(0))
    assert t.tns_lookup(52.09, -27.38)["objname"] == "2026afma"


def test_a_dropped_call_then_a_none_is_a_none(clock, monkeypatch):
    asked = fake.serve(monkeypatch, fake.REJECTED, fake.NONE)
    assert t.tns_lookup(10.0, -45.0) == {} and len(asked) == 2


def test_rejected_throughout_is_exactly_nine_asks_then_unavailable(clock, monkeypatch):
    """
    Literal values, never the constants, so changing the pause, the wait or the limit turns
    this red. The asks start at 6.5, 28, 49.5 ... 178.5 s; a tenth would start at 200 s. The
    count alone moves in steps (a limit of 185 s asks nine times too), so the settings
    are pinned as well.
    """
    asked = fake.serve(monkeypatch, fake.REJECTED)
    assert t.tns_lookup(52.09, -27.38) is None
    assert len(asked) == 9
    assert clock.slept == [6.5] + [15.0, 6.5] * 8
    assert (t.TNS_PAUSE, t.TNS_THROTTLE_WAIT, t.TNS_MAX_WAIT) == (6.5, 15.0, 180.0)


def test_a_hanging_service_costs_the_limit_in_real_time_not_a_count_of_waits(clock, monkeypatch):
    """
    Each ask hangs for the request timeout, then fails. Counting only the waits, this would
    be nine asks and over ten minutes per alert; a day's alerts could reach the unit's
    timeout before the state is saved. Asks start at 6.5, 88 and 169.5 s; the last ends at 229.5 s.
    """
    asks = []

    def hangs(url, json, timeout):
        asks.append(clock.t)
        clock.t += timeout
        raise requests.Timeout("hung")

    monkeypatch.setattr(t.requests, "post", hangs)
    start = clock.t
    assert t.tns_lookup(52.09, -27.38) is None
    assert [a - start for a in asks] == [6.5, 88.0, 169.5]
    assert clock.t - start == 229.5 <= t.TNS_MAX_WAIT + t.REQUEST_TIMEOUT


# --- own ID ------------------------------------------------------------------------------------


@pytest.mark.parametrize("names,oid,listed", [
    (",LSST-P-DO-171141216521945181", "171141216521945181", True),  # SN 2026afma's real value, leading comma included
    ("LSST-AP-DO-171141216668745796", "171141216668745796", True),  # AT 2026ahly's
    ("GOTO26bpn,ZTF26aajowek,ATLAS26crp,BGEM J100453.35-000329.2,PS26axk", "ZTF26aajowek", True),  # SN 2026evy's: bare
    ("LSST-AP-DO-91234", "1234", False),  # the tail of another ID
    ("YSE26xyz", "1234", False),
    ("", "1234", False),
    (None, "1234", False),
])
def test_own_id_listed_reads_both_ways_surveys_write_their_ids(names, oid, listed):
    assert t.own_id_listed({"internal_names": names}, oid) is listed


def test_own_id_listed_on_a_record_without_the_field():
    assert t.own_id_listed({}, "1234") is False
