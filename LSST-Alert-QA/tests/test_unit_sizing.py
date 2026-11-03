"""
Each pipeline unit's TimeoutStartSec against the longest run its own settings allow.

A run stopped at its deadline still writes its partial CSV; a unit killed at
TimeoutStartSec writes none. So the start timeout has to outlast everything a run can
spend before the deadline stops it, and the object in flight when it does:

    ExecStartPre (nm-online's -t)  +  the listing's worst case
    +  deadline_for(n, survey)     +  one object in flight at the deadline

The deadline's clock starts after the listing (reporting.run_pipeline,
run_antares_pipeline), and it is checked between objects, so one object can run on past
it. Worst cases come from the settings: a retried call is RETRY_ATTEMPTS calls of up to
REQUEST_TIMEOUT each, plus its backoff (RETRY_DELAY, doubling). The ANTARES listing is
many page requests inside one retried call, so each attempt is its healthy pages plus
the stall that ends it.

Two limits:
  - It reads the .example files in systemd/, which mirror the installed units but are
    not them. The installed units are checked with
        systemctl --user show -p TimeoutStartUSec lsst-pipeline-alerce lsst-pipeline-antares
  - The ANTARES listing's healthy pages are bounded by one REQUEST_TIMEOUT because that
    was measured at n = 256 (42.5 s: README, "Known API Quirks"). A unit asking more
    fails here instead of being assumed to fit.

An exception to "units are tested by the local install, never by pytest" (CLAUDE.md):
this checks the units' sizing against the code's deadline, which no install can see.
"""

import re
from pathlib import Path

import pytest

from rubin_qa.config import INTER_OBJECT_DELAY, REQUEST_TIMEOUT, RETRY_ATTEMPTS, RETRY_DELAY
from rubin_qa.reporting import deadline_for

SYSTEMD = Path(__file__).resolve().parents[1] / "systemd"
PIPELINE_UNITS = ("lsst-pipeline-alerce", "lsst-pipeline-antares")
# Requests per object: (retried through _api_call, not retried). ALeRCE: one retried call
# per endpoint in client.fetch_object_data, LSST's magstats refused by the client with no
# request. ANTARES: the locus (retried), then its alerts, fetched lazily outside the retry
# (antares_api.fetch_antares_locus).
REQUESTS_PER_OBJECT = {"ztf": (3, 0), "lsst": (2, 0), "antares": (1, 1)}
ANTARES_LISTING_MEASURED_AT = 256  # loci; the listing bound below holds up to here
TIMESPAN_UNITS = {"h": 3600, "min": 60, "s": 1, "ms": 0.001}


def setting(unit: str, key: str) -> str:
    for line in (SYSTEMD / f"{unit}.service.example").read_text().splitlines():
        k, sep, v = line.partition("=")
        if sep and k.strip() == key:
            return v.strip()
    raise AssertionError(f"{unit}: no {key}")


def seconds(span: str) -> float:
    """A systemd time span ('20min', '1h 5min', '90s') in seconds."""
    parts = re.findall(r"(\d+)\s*(h|min|ms|s)\b", span)
    assert parts and re.fullmatch(r"(\s*\d+\s*(h|min|ms|s))+\s*", span), f"cannot read the time span {span!r}"
    return sum(int(v) * TIMESPAN_UNITS[u] for v, u in parts)


def backoff() -> float:
    """The sleeps of one retried call: RETRY_DELAY, doubling, between its attempts."""
    return sum(RETRY_DELAY * 2 ** i for i in range(RETRY_ATTEMPTS - 1))


def worst_call() -> float:
    return RETRY_ATTEMPTS * REQUEST_TIMEOUT + backoff()


def worst_listing(survey: str, n: int) -> float:
    if survey != "antares":
        return worst_call()  # one listing request (query_objects), retried like any call
    assert n <= ANTARES_LISTING_MEASURED_AT, (
        f"the ANTARES listing was measured at n = {ANTARES_LISTING_MEASURED_AT}, the unit asks {n}: "
        "measure the listing at that size before trusting this bound")
    # each attempt: the healthy pages (within one REQUEST_TIMEOUT at n <= 256), then the stall
    return RETRY_ATTEMPTS * (REQUEST_TIMEOUT + REQUEST_TIMEOUT) + backoff()


def worst_object(survey: str) -> float:
    retried, plain = REQUESTS_PER_OBJECT[survey]
    return retried * worst_call() + plain * REQUEST_TIMEOUT + INTER_OBJECT_DELAY


def pre_start(unit: str) -> float:
    """What ExecStartPre may take: nm-online's -t."""
    pre = setting(unit, "ExecStartPre")
    m = re.search(r"nm-online\b.*\s-t\s+(\d+)", pre)
    assert m, f"{unit}: ExecStartPre is not an nm-online wait ({pre!r}): bound it here"
    return float(m[1])


def run_bound(unit: str) -> tuple[float, str]:
    """The longest the unit's run may take, and how it adds up."""
    survey, n = setting(unit, "ExecStart").split(" run ", 1)[1].split()[1:3]
    n = int(n)
    parts = {"ExecStartPre": pre_start(unit), "listing": worst_listing(survey, n),
             f"deadline_for({n}, {survey})": deadline_for(n, survey), "object in flight": worst_object(survey)}
    return sum(parts.values()), " + ".join(f"{k} {v:.0f}" for k, v in parts.items())


@pytest.mark.parametrize("unit", PIPELINE_UNITS)
def test_the_start_timeout_outlasts_the_deadline(unit):
    bound, how = run_bound(unit)
    timeout = seconds(setting(unit, "TimeoutStartSec"))
    assert bound < timeout, (
        f"{unit}: TimeoutStartSec {timeout:.0f}s would kill the run before its deadline can stop it "
        f"with a partial CSV: {how} = {bound:.0f}s")


def test_the_time_span_reader():
    assert seconds("20min") == 1200 and seconds("1h 5min") == 3900 and seconds("90s") == 90
    with pytest.raises(AssertionError):
        seconds("infinity")
