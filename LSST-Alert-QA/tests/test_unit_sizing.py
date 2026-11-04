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
the broker's timeout per request each, plus its backoff (RETRY_DELAY, doubling). ALeRCE's
is REQUEST_TIMEOUT, forced onto its client; ANTARES's is antares-client's own,
ANTARES_CLIENT_TIMEOUT, for the listing and every object request alike.

The ANTARES listing is bounded by antares-client's own timeout per request (user,
2026-11-04): a request the server never answers ends only there, so that is the real
ceiling. The server's cutoff (a 500 after about 10 s: README, "Known API Quirks") is how a
slow request usually ends, not the bound; a measured p95 would bound only a healthy
server. The listing is one retried call of ceil(n / ANTARES_MAX_PAGE_SIZE) pages, each page
after the first one more request for its http:// redirect: every request up to the
client's timeout, on every attempt, plus the backoffs. The retry budget caps the waits
only, never the requests (retry_budget.call), and the listing is the run's first call, so
it gets every attempt.

Four limits:
  - It reads the .example files in systemd/, which mirror the installed units but are
    not them. The installed units are checked with
        systemctl --user show -p TimeoutStartUSec lsst-pipeline-alerce lsst-pipeline-antares
  - The client's timeout is pinned in this test's process; the units inherit the user
    manager's environment, so no .example may set API_TIMEOUT or load an EnvironmentFile.
  - A redirect is counted as a full request, the cautious side: it is a 301, not a search.
  - A timeout bounds each phase of a request (connect, each read), not a response that
    keeps trickling in. Both brokers' bounds assume it (README: REQUEST_TIMEOUT "bounds
    inactivity, not call duration").

An exception to "units are tested by the local install, never by pytest" (CLAUDE.md):
this checks the units' sizing against the code's deadline, which no install can see.
"""

import math
import re
import sys
from pathlib import Path

import pytest

from rubin_qa.antares_api import ANTARES_MAX_PAGE_SIZE
from rubin_qa.config import INTER_OBJECT_DELAY, REQUEST_TIMEOUT, RETRY_ATTEMPTS, RETRY_BUDGET_SECONDS, RETRY_DELAY
from rubin_qa.reporting import deadline_for

SYSTEMD = Path(__file__).resolve().parents[1] / "systemd"
PIPELINE_UNITS = ("lsst-pipeline-alerce", "lsst-pipeline-antares")
# Requests per object: (retried through _api_call, not retried). ALeRCE: one retried call
# per endpoint in client.fetch_object_data, LSST's magstats refused by the client with no
# request. ANTARES: the locus (retried), then its alerts, fetched lazily outside the retry
# (antares_api.fetch_antares_locus).
REQUESTS_PER_OBJECT = {"ztf": (3, 0), "lsst": (2, 0), "antares": (1, 1)}
# seconds: the longest one ANTARES request may take, antares-client's own timeout: API_TIMEOUT
# in antares_client/config.py, passed by search.py to its API client, where it overrides the
# v2 client's own 30 s. Read from the installed client below, so a change turns red.
ANTARES_CLIENT_TIMEOUT = 60.0
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


def request_timeout(survey: str) -> float:
    """The longest one request to the survey's broker may take: antares-client's own timeout, or ALeRCE's forced one."""
    return ANTARES_CLIENT_TIMEOUT if survey == "antares" else REQUEST_TIMEOUT


def worst_call(survey: str) -> float:
    return RETRY_ATTEMPTS * request_timeout(survey) + backoff()


def listing_requests(n: int) -> int:
    """Requests one ANTARES listing attempt sends: its pages, and a redirect before each page after the first."""
    pages = math.ceil(n / ANTARES_MAX_PAGE_SIZE)
    return pages + (pages - 1)


def worst_listing(survey: str, n: int) -> float:
    if survey != "antares":
        return worst_call(survey)  # one listing request (query_objects), retried like any call
    return RETRY_ATTEMPTS * listing_requests(n) * ANTARES_CLIENT_TIMEOUT + min(backoff(), RETRY_BUDGET_SECONDS)


def worst_object(survey: str) -> float:
    retried, plain = REQUESTS_PER_OBJECT[survey]
    return retried * worst_call(survey) + plain * request_timeout(survey) + INTER_OBJECT_DELAY


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


@pytest.mark.parametrize("n,requests", [(256, 1), (ANTARES_MAX_PAGE_SIZE, 1), (ANTARES_MAX_PAGE_SIZE + 1, 3)])
def test_a_listing_up_to_the_cap_is_one_request(n, requests):
    assert listing_requests(n) == requests


def test_the_installed_client_times_out_where_the_bound_says():
    """Every phase of a request (connect, read, write, pool) ends at the client's timeout, the bound's value."""
    from antares_client import search

    t = search.api_client.client.timeout
    assert (t.connect, t.read, t.write, t.pool) == (ANTARES_CLIENT_TIMEOUT,) * 4


def test_the_antares_listing_is_bounded_by_the_clients_timeout_per_request():
    """n = 256 is one request: every attempt up to the client's timeout, plus the waits; the budget caps only those."""
    assert worst_listing("antares", 256) == RETRY_ATTEMPTS * 1 * ANTARES_CLIENT_TIMEOUT + backoff()


def test_an_antares_object_is_bounded_by_the_clients_timeout_not_alerces(monkeypatch):
    """
    REQUEST_TIMEOUT is ALeRCE's, forced onto its client; ANTARES requests end at
    antares-client's own. Both are 60 s today, so REQUEST_TIMEOUT is moved here to tell them apart.
    """
    antares, ztf = worst_object("antares"), worst_object("ztf")
    assert antares == (RETRY_ATTEMPTS * ANTARES_CLIENT_TIMEOUT + backoff()) + ANTARES_CLIENT_TIMEOUT + INTER_OBJECT_DELAY
    monkeypatch.setattr(sys.modules[__name__], "REQUEST_TIMEOUT", 1.0)
    assert worst_object("antares") == antares
    assert worst_object("ztf") < ztf  # ALeRCE's bound does follow REQUEST_TIMEOUT


def test_no_unit_changes_the_clients_timeout():
    """The pin above holds for this process; a unit inherits the user manager's environment, plus what it sets."""
    for path in sorted(SYSTEMD.glob("*.example")):
        for line in path.read_text().splitlines():
            key, sep, value = line.partition("=")
            assert not (sep and key.strip() == "EnvironmentFile"), f"{path.name}: {line!r} may set API_TIMEOUT"
            assert not (sep and key.strip() == "Environment" and "API_TIMEOUT" in value), f"{path.name}: {line!r}"


def test_the_time_span_reader():
    assert seconds("20min") == 1200 and seconds("1h 5min") == 3900 and seconds("90s") == 90
    with pytest.raises(AssertionError):
        seconds("infinity")
