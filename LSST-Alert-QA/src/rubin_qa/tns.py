"""
TNS lookups through ALeRCE's TNS service (tns.alerce.online, by position, no credentials).

Moved here from tools/host_match_check.py on 2026-10-02, when the transient monitor's
point-source watch needed the same lookup: one implementation of the rule. How the service
answers, and the model this reading replaced: README, "Replay Validation" (the oracle).
"""

from __future__ import annotations

import sys
import time
from urllib.parse import unquote

import requests

from .config import REQUEST_TIMEOUT, WARN_PREFIX

ALERCE_TNS_URL = "https://tns.alerce.online/search"
# seconds before every ask: this client alone stays under 10 a minute. A courtesy, not a
# guarantee: calls are rejected below that rate too (README, "Replay Validation").
TNS_PAUSE = 6.5
TNS_THROTTLE_WAIT = 15.0  # seconds between asks after a non-answer, on top of TNS_PAUSE
# seconds of real time in which a lookup may start its asks; how it is used: tns_lookup
TNS_MAX_WAIT = 180.0
TNS_BODY_LOG_BYTES = 200  # how much of a body of no known shape goes into the journal


# ALeRCE's TNS service passes some names through URL-encoded: 2026fgl's host came as
# 2MASXJ09565234%2B0328119, i.e. ...+0328119. Decoded here, where the names come in, and
# nowhere else: decoding twice would mangle a name with a literal %. unquote, not
# unquote_plus, so a literal + in a J-name survives.
TNS_NAME_FIELDS = ("objname", "hostname", "internal_names", "discoverer_internal_name")


class TnsUnavailable(Exception):
    """The service answered, but with neither a record nor "none": a rejected call or an upstream failure."""


class TnsUnknownBody(TnsUnavailable):
    """The service answered with a body of no known shape."""


def tns_once(ra: float, dec: float) -> dict:
    """
    One call, no retry. The service answers HTTP 200 whatever happened, and the body says
    which: object_data holds a record (returned, names decoded); id_code 200 with data []
    is "none" (returned as {}, the only thing that ever is); id_code 429 is a rejected call
    and an error key an upstream failure (TnsUnavailable). Anything else is TnsUnknownBody,
    its first bytes warned about, so a new shape shows up in the journal and can never read
    as "none". Raising, not returning a value, so no caller's `if not rec` can take a
    non-answer for "none". A failed request raises requests.RequestException.
    """
    r = requests.post(ALERCE_TNS_URL, json={"ra": ra, "dec": dec}, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    try:
        body = r.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        rec = body.get("object_data")
        if rec and isinstance(rec, dict):
            return {k: unquote(v) if k in TNS_NAME_FIELDS and isinstance(v, str) else v for k, v in rec.items()}
        if body.get("id_code") == 200 and body.get("data") == []:
            return {}
        if body.get("id_code") == 429:
            raise TnsUnavailable("rejected (429)")
        if "error" in body:
            raise TnsUnavailable(f"upstream failure: {str(body['error'])[:TNS_BODY_LOG_BYTES]}")
    head = r.text[:TNS_BODY_LOG_BYTES]
    print(f"{WARN_PREFIX}TNS answered with a body of no known shape: {head!r}", file=sys.stderr)
    raise TnsUnknownBody(head)


def own_id_listed(rec: dict, survey_object_id: str) -> bool:
    """
    Does the record list this survey object among its internal names? ZTF lists the bare
    ID; Rubin's reports carry it after a prefix (LSST-AP-DO-<diaObjectId>, LSST-P-DO-<...>).
    """
    names = rec.get("internal_names")
    if not isinstance(names, str):
        return False
    listed = [n.strip() for n in names.split(",")]
    return any(n == survey_object_id or n.endswith(f"-{survey_object_id}") for n in listed)


def tns_lookup(ra: float, dec: float) -> dict | None:
    """
    The TNS record at a position; {} when the service says it has none; None when
    unavailable. A rejected call, an upstream failure or a failed request is temporary, so
    it is asked again every TNS_THROTTLE_WAIT, as long as the next ask starts within
    TNS_MAX_WAIT of the lookup's start. That is real time, not a count of the waits: a
    hanging ask costs REQUEST_TIMEOUT, and a service that hangs must not hold each alert for
    that many times over. So a lookup ends at most REQUEST_TIMEOUT past TNS_MAX_WAIT. A body
    of no known shape may not be temporary and is not asked again.
    """
    deadline = time.monotonic() + TNS_MAX_WAIT
    while True:
        time.sleep(TNS_PAUSE)
        try:
            return tns_once(ra, dec)
        except TnsUnknownBody:
            return None
        except (TnsUnavailable, requests.RequestException):
            if time.monotonic() + TNS_THROTTLE_WAIT + TNS_PAUSE > deadline:
                return None
            time.sleep(TNS_THROTTLE_WAIT)


def tns_batch(positions: list[tuple[float, float]]) -> list[dict | None]:
    """tns_lookup for each position (paced inside)."""
    return [tns_lookup(ra, dec) for ra, dec in positions]
