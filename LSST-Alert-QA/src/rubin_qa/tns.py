"""
TNS lookups through ALeRCE's TNS service (tns.alerce.online, by position, no credentials).

Moved here from tools/host_match_check.py on 2026-10-02, when the transient monitor's
point-source watch needed the same throttle-safe lookup: one implementation of the rule.
"""

from __future__ import annotations

import time
from urllib.parse import unquote

import requests

ALERCE_TNS_URL = "https://tns.alerce.online/search"
REQUEST_TIMEOUT = 60
# A TNS object ALeRCE's TNS service must know, asked to tell "not in TNS" from "throttled".
# The service runs on a quota of 10 answers per 60 s window and answers 200 with an empty
# record once it is spent, until the window turns (measured 2026-10-01: exactly 10 full
# answers per window, in fixed windows that turned at :23 past the minute that day).
# Within a window, answers only go full -> empty, so an empty answer followed by a full
# canary in the *same* window is genuine. Where the windows turn is never used: a server
# restart can move it. Ask twice instead: two (empty answer, full canary) pairs within
# one window length cannot both straddle a turn, so at least one shares a window and
# vouches for "not in TNS". A canary from before the answer proves nothing (the quota can
# run out in between). The rule assumes fixed windows at least TNS_WINDOW_SECONDS long;
# tests sweep every phase against a simulated service, and a shorter window fools it. (User's design,
# 2026-10-01, the canary at the scale of the cycle; it replaced
# two 10 s retries per object, then a canary bracketing the whole batch, which a
# throttled minute in the middle slips past.)
TNS_CANARY = {"name": "2025zi", "ra": 54.727480, "dec": -26.370617}
TNS_WINDOW_SECONDS = 60
TNS_PAUSE = 6.5  # before every call, canaries included: under 10 a window, so we do not spend the quota ourselves
TNS_THROTTLE_WAIT = 15.0  # a throttled canary is asked again this often, until it answers
TNS_MAX_WAIT = 180.0  # throttled for longer than this: "unavailable"


# ALeRCE's TNS service passes some names through URL-encoded: 2026fgl's host came as
# 2MASXJ09565234%2B0328119, i.e. ...+0328119. Decoded here, where the names come in, and
# nowhere else: decoding twice would mangle a name with a literal %. unquote, not
# unquote_plus, so a literal + in a J-name survives.
TNS_NAME_FIELDS = ("objname", "hostname", "internal_names", "discoverer_internal_name")


def tns_once(ra: float, dec: float) -> dict:
    """The TNS record at a position, via ALeRCE, names decoded; {} when it answers without one. One call, no retry."""
    r = requests.post(ALERCE_TNS_URL, json={"ra": ra, "dec": dec}, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    rec = r.json().get("object_data") or {}
    return {k: unquote(v) if k in TNS_NAME_FIELDS and isinstance(v, str) else v for k, v in rec.items()}


def tns_canary_ok() -> bool:
    time.sleep(TNS_PAUSE)
    try:
        return tns_once(TNS_CANARY["ra"], TNS_CANARY["dec"]).get("objname") == TNS_CANARY["name"]
    except requests.RequestException:
        return False


def _now() -> float:
    return time.time()


def tns_lookup(ra: float, dec: float) -> dict | None:
    """
    The TNS record at a position; {} only when vouched for as "not in TNS" (see
    TNS_CANARY); None when unavailable: the request failed, or the service stayed
    throttled past TNS_MAX_WAIT.
    """
    pairs: list[float] = []  # when each (empty answer, full canary) pair started
    waited = 0.0
    while True:
        time.sleep(TNS_PAUSE)
        asked = _now()
        try:
            rec = tns_once(ra, dec)
        except requests.RequestException:
            return None
        if rec:
            return rec
        if tns_canary_ok():
            pairs.append(asked)
            if len(pairs) >= 2 and _now() - pairs[-2] < TNS_WINDOW_SECONDS:
                return {}
            continue
        # throttled: wait until the canary answers again, then ask again (earlier pairs
        # still count: the window argument holds for any two)
        while True:
            if waited >= TNS_MAX_WAIT:
                return None
            time.sleep(TNS_THROTTLE_WAIT)
            waited += TNS_THROTTLE_WAIT
            if tns_canary_ok():
                break


def tns_batch(positions: list[tuple[float, float]]) -> list[dict | None]:
    """tns_lookup for each position (paced inside)."""
    return [tns_lookup(ra, dec) for ra, dec in positions]
