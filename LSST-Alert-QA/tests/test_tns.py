"""
rubin_qa/tns.py - TNS lookups through ALeRCE's throttled TNS service.

The service answers 200 with an empty record once its quota of 10 a minute is spent, so
"throttled" looks exactly like "not in TNS". tns_lookup accepts an empty answer only when
two (empty answer, full canary) pairs fall within one window length. These tests feed it a
fake clock and a simulated throttled service; no network. Moved here from
test_host_match_check.py on 2026-10-02 with the code (the monitor's point-source watch
uses the lookup too).
"""

from types import SimpleNamespace

import pytest
import requests

from rubin_qa import tns as t

CANARY_REC = {"objname": t.TNS_CANARY["name"], "hostname": "NGC1398"}


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
    """A fake time for tns_lookup: each _now() call takes the next value; sleeps are recorded."""
    times, slept = [], []
    monkeypatch.setattr(t, "_now", lambda: times.pop(0))
    monkeypatch.setattr(t.time, "sleep", slept.append)
    return times, slept


def answering(*answers, calls=None):
    seq = iter(answers)

    def once(ra, dec):
        if calls is not None:
            calls.append((ra, dec))
        return next(seq)
    return once


def test_two_vouched_empty_answers_within_one_window_are_not_in_tns(clock, monkeypatch):
    clock[0].extend([0.0, 14.0, 21.0])  # first asked, second asked, checked
    calls = []
    monkeypatch.setattr(t, "tns_once", answering({}, CANARY_REC, {}, CANARY_REC, calls=calls))
    assert t.tns_lookup(150.0, 2.0) == {}
    assert len(calls) == 4


def test_a_full_answer_needs_no_canary(clock, monkeypatch):
    clock[0].append(0.0)
    calls = []
    monkeypatch.setattr(t, "tns_once", answering({"objname": "2026xyz"}, calls=calls))
    assert t.tns_lookup(150.0, 2.0) == {"objname": "2026xyz"} and len(calls) == 1


def test_one_pair_is_never_enough_it_may_straddle_a_window_turn(clock, monkeypatch):
    """Quota spent: the answer is empty, the canary lands in the next window and is full; the re-ask finds the record."""
    clock[0].extend([0.0, 14.0])
    monkeypatch.setattr(t, "tns_once", answering({}, CANARY_REC, {"objname": "2026xyz"}))
    assert t.tns_lookup(150.0, 2.0) == {"objname": "2026xyz"}


def test_pairs_further_apart_than_a_window_do_not_vouch(clock, monkeypatch):
    clock[0].extend([0.0, 40.0, 61.0, 62.0, 75.0])  # pairs at 0 and 40 end at 61: two turns possible; at 40 and 62: fine
    calls = []
    monkeypatch.setattr(t, "tns_once", answering({}, CANARY_REC, {}, CANARY_REC, {}, CANARY_REC, calls=calls))
    assert t.tns_lookup(150.0, 2.0) == {}
    assert len(calls) == 6


def test_a_throttled_canary_is_polled_until_it_answers_then_the_position_is_asked_again(clock, monkeypatch):
    clock[0].extend([0.0, 40.0, 47.0, 54.0])  # first asked; after the throttle: asked, asked, checked
    calls = []
    monkeypatch.setattr(t, "tns_once", answering(
        {}, {},  # the answer, its canary: throttled
        {}, CANARY_REC,  # the canary polled until it answers; the position is not asked meanwhile
        {}, CANARY_REC, {}, CANARY_REC,  # then two pairs within one window
        calls=calls))
    assert t.tns_lookup(150.0, 2.0) == {}
    assert [w for w in clock[1] if w == t.TNS_THROTTLE_WAIT] == [t.TNS_THROTTLE_WAIT] * 2
    canary = (t.TNS_CANARY["ra"], t.TNS_CANARY["dec"])
    assert [c == canary for c in calls] == [False, True, True, True, False, True, False, True]


def test_throttled_throughout_is_unavailable_never_not_in_tns(clock, monkeypatch):
    clock[0].extend([0.0] * 100)
    monkeypatch.setattr(t, "tns_once", lambda ra, dec: {})
    assert t.tns_lookup(150.0, 2.0) is None
    assert sum(w for w in clock[1] if w == t.TNS_THROTTLE_WAIT) == t.TNS_MAX_WAIT  # waited it out, then gave up


def test_a_failed_request_is_unavailable_and_a_failed_canary_is_throttled(clock, monkeypatch):
    def down(ra, dec):
        raise requests.ConnectionError("TNS down")

    monkeypatch.setattr(t, "tns_once", down)
    clock[0].append(0.0)
    assert t.tns_lookup(150.0, 2.0) is None
    monkeypatch.setattr(t, "tns_once", lambda ra, dec: down(ra, dec) if ra == t.TNS_CANARY["ra"] else {})
    clock[0].extend([0.0] * 100)
    assert t.tns_lookup(150.0, 2.0) is None  # never vouched for: never "not in TNS"


class ThrottledTNS:
    """
    ALeRCE's TNS proxy as measured 2026-10-01: `quota` answers per fixed `window`-second
    window, then empty records until it turns. The windows start at `phase` (observed
    at :23 past the minute, which a restart can move), and other users spend whatever is
    left at `others_at` seconds into every window. Time advances only through calls and sleeps.
    """

    def __init__(self, phase: float, others_at: float | None, quota: int = 10, window: float = 60.0):
        self.t, self.phase, self.others_at, self.quota, self.window = 1000.0, phase, others_at, quota, window
        self.used: dict[int, int] = {}

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s

    def once(self, ra: float, dec: float) -> dict:
        self.t += 0.9  # one call
        w, into = divmod(self.t - self.phase, self.window)
        spent = self.others_at is not None and into >= self.others_at
        if spent or self.used.get(w, 0) >= self.quota:
            return {}
        self.used[w] = self.used.get(w, 0) + 1
        if (ra, dec) == (t.TNS_CANARY["ra"], t.TNS_CANARY["dec"]):
            return CANARY_REC
        return {"objname": "2026xyz"} if ra == 150.0 else {}


def sweep(monkeypatch, window: float = 60.0):
    """tns_lookup against every window phase and every moment others spend the quota."""
    for phase in [p / 2 for p in range(120)]:
        for others_at in [None, *range(0, 60, 3)]:
            sim = ThrottledTNS(phase, others_at, window=window)
            monkeypatch.setattr(t, "_now", sim.now)
            monkeypatch.setattr(t.time, "sleep", sim.sleep)
            monkeypatch.setattr(t, "tns_once", sim.once)
            yield phase, others_at, t.tns_lookup(150.0, 2.0), t.tns_lookup(151.0, 2.0)


def test_throttling_is_never_taken_for_not_in_tns_whatever_the_window_phase(monkeypatch):
    """The rule may rely on the window length, never on where the windows turn."""
    outcomes = {"found": 0, "not_in_tns": 0, "unavailable": 0}
    for phase, others_at, known, unknown in sweep(monkeypatch):
        assert known != {}, f"a known object passed as 'not in TNS': phase {phase}, others spend at {others_at}"
        assert unknown in ({}, None)
        outcomes["found"] += known is not None
        outcomes["not_in_tns"] += unknown == {}
        outcomes["unavailable"] += unknown is None
    assert outcomes["found"] and outcomes["not_in_tns"]  # the sweep exercises both answers, not only "unavailable"
