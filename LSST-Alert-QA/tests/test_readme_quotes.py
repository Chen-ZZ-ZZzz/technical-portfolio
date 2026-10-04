"""
The docs' guarded quotes (user, 2026-10-04).

The README quotes a code constant in one case only: a recorded result, the outcome of
a validation or a measurement, which the README holds as evidence and the code then
implements. Each such result is one row below: a pattern that finds every place the
doc quotes it (alternatives each capture the number), and the value it must equal. If
the code changes, the README's justification is stale, and the row says so. A row
whose pattern no longer matches fails too: nothing would be guarded. A row with
several quotes cannot see one of them reworded: it goes on guarding the others.
Operational settings (timeouts, retries, deadlines) are not quoted: the README points
to them (CLAUDE.md, "Documentation: one home per fact").

The same table guards systemd/README.md's summary of the unit files (cadence, command,
start timeouts) against the files, its "frozen" against the files test_sso_frozen pins
(active and dormant cannot be read from a file: arming is the install), and the README's
antares-client version against the lock. It checks what the docs say about the units, never that a unit works: that is
the user's install.

Adding the next guarded quote is one row.
"""

import re
import tomllib
from pathlib import Path

import pytest

from rubin_qa import classifier, replay, sky_catalog, tns, transient_monitor
from rubin_qa.config import DEFAULT_SECONDS_PER_OBJECT, SECONDS_PER_OBJECT
from tests.test_sso_frozen import FROZEN_FILES

ROOT = Path(__file__).resolve().parents[1]
SYSTEMD = ROOT / "systemd"
DOCS = {name: (ROOT / name).read_text() for name in ("README.md", "systemd/README.md")}


def _latency_row(survey: str) -> str:
    """The campaign table's row for a survey: its last column is the constant."""
    return rf"\| {survey} \|(?:[^|\n]*\|){{4}} \*\*([\d.]+)s\*\* \|"


def unit(name: str, key: str) -> str:
    """A setting from a unit file in systemd/."""
    for line in (SYSTEMD / name).read_text().splitlines():
        k, sep, v = line.partition("=")
        if sep and k.strip() == key:
            return v.strip()
    return f"<no {key} in {name}>"


def cadence(timer: str) -> str:
    """The table's word for a timer's OnCalendar."""
    when = unit(timer, "OnCalendar")
    m = re.fullmatch(r"(?:(\w{3}) )?\*-\*-\* \d\d:\d\d:\d\d", when)
    return ("weekly" if m[1] else "daily") if m else when


def runs(service: str) -> str:
    """What a service runs: its ExecStart after `uv run`."""
    return unit(service, "ExecStart").split(" run ", 1)[-1]


def agreed(*values: str) -> str:
    """One value quoted for several files; if they differ, the row fails on the mismatch."""
    return values[0] if len(set(values)) == 1 else " / ".join(values)


def frozen(timer: str) -> tuple[str, ...]:
    """('frozen',) for a unit whose files are SHA-pinned, else nothing: the status column's marker."""
    return ("frozen",) if any(f"systemd/{timer}.{s}" in FROZEN_FILES for s in ("timer", "service.example")) else ()


def locked(package: str) -> str:
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    return next(p["version"] for p in lock["package"] if p["name"] == package)


TIMERS = sorted(p.stem for p in SYSTEMD.glob("*.timer"))
MONITOR_TIMER = "lsst-transient-monitor.timer"

GUARDED = {
    "README.md": [
        # (what, pattern with the quoted value as its group(s), the value)
        ("PASS threshold",
         r"`PASS` \| No issues, consensus ≥ ([\d.]+)|consensus ≥ ([\d.]+) \| `PASS`",
         classifier.HIGH_CONFIDENCE_THRESHOLD),
        ("majority threshold",
         r"`REVIEW_MINOR` \| No issues, consensus ≥ ([\d.]+)|consensus ≥ ([\d.]+), all dissenters"
         r"|([\d.]+) majority threshold",
         classifier.MAJORITY_THRESHOLD),
        ("outlier probability", r"(?:below|<) prob ([\d.]+)", classifier.OUTLIER_PROB_THRESHOLD),
        ("host threshold", r"threshold d_DLR ≤ (\d+)", sky_catalog.HOST_DLR_MAX),
        ("host search radius", r"beyond the (\d+)″ search|Tractor sources within (\d+)″",
         sky_catalog.SEARCH_RADIUS_ARCSEC),
        ("catalogue-gap area", r"BAILOUT in its (\d+)″ search area", sky_catalog.GAP_RADIUS_ARCSEC),
        ("WISE S/N", r"both WISE bands have S/N ≥ (\d+)", sky_catalog.WISE_SNR_MIN),
        ("AGN colour", r"converted to ([\d.]+) AB", sky_catalog.AGN_W1W2_AB_MIN),
        ("point-source watch S/N", r"point source below S/N (\d+)", transient_monitor.PS_REVISIT_SNR),
        ("lookback", r"(\d+)-day lookback", transient_monitor.LOOKBACK_DAYS),
        ("association radius", r"([\d.]+)″ association radius", transient_monitor.ASSOC_RADIUS_ARCSEC),
        ("light-curve call", r"measured ([\d.]+) s per light-curve call", replay.SECONDS_PER_CALL),
        ("TNS window", r"requests per (\d+) s window|pairs fall within (\d+) s|its assumed (\d+) s",
         tns.TNS_WINDOW_SECONDS),
        ("seconds per object, ztf", _latency_row("ztf"), SECONDS_PER_OBJECT["ztf"]),
        ("seconds per object, lsst", _latency_row("lsst"), SECONDS_PER_OBJECT["lsst"]),
        ("seconds per object, antares", _latency_row("antares"), SECONDS_PER_OBJECT["antares"]),
        ("unmeasured-survey fallback", r"The ([\d.]+)s figure was an episode", DEFAULT_SECONDS_PER_OBJECT),
        ("antares-client version", r"antares-client ([\d.]+) \(the pinned version\)", locked("antares-client")),
    ],
    "systemd/README.md": [
        *[(f"unit table, {t}", rf"(?m)^\| `{t}` \| (\w+)[^|]*\| `([^`]+)` \| (?:\*\*(frozen)\*\*)?",
           (cadence(f"{t}.timer"), runs(f"{t}.service.example"), *frozen(t))) for t in TIMERS],
        ("monitor's hour",
         r"daily, (\d\d:\d\d) \(your choice\)|The example says (\d\d:\d\d)|so (\d\d:\d\d) leaves"
         r"|`\*-\*-\* (\d\d:\d\d):00 Europe/Berlin`",
         unit(MONITOR_TIMER, "OnCalendar").split()[-1][:5]),
        ("sampler timeout", r"`(\w+)` for the sampler",
         unit("lsst-latency-sample.service.example", "TimeoutStartSec")),
        ("pipelines timeout", r"`(\w+)` for the pipelines",
         agreed(*(unit(f"lsst-pipeline-{b}.service.example", "TimeoutStartSec") for b in ("alerce", "antares")))),
        ("monitor timeout", r"`(\w+)` for the transient monitor",
         unit("lsst-transient-monitor.service.example", "TimeoutStartSec")),
        ("sampler schedule", r"`OnCalendar=(\w+)` \+ `RandomizedDelaySec=(\d+)`",
         (unit("lsst-latency-sample.timer", "OnCalendar"), unit("lsst-latency-sample.timer", "RandomizedDelaySec"))),
    ],
}


def quotes(pattern: str, text: str) -> list[tuple[str, ...]]:
    """Every quote the pattern finds: the groups of each match, whichever alternative matched."""
    return [tuple(g for g in m.groups() if g is not None) for m in re.finditer(pattern, text)]


def says(quote: tuple[str, ...], value) -> bool:
    expected = value if isinstance(value, tuple) else (value,)
    return len(quote) == len(expected) and all(
        float(q) == pytest.approx(v) if isinstance(v, (int, float)) else q == v for q, v in zip(quote, expected))


@pytest.mark.parametrize("doc,pattern,value", [
    pytest.param(doc, p, v, id=f"{doc}: {what}") for doc, rows in GUARDED.items() for what, p, v in rows])
def test_the_docs_quote_what_they_describe(doc, pattern, value):
    found = quotes(pattern, DOCS[doc])
    assert found, f"{doc} no longer quotes it ({pattern!r}): drop the row, or the quote was reworded"
    assert all(says(q, value) for q in found), f"{doc} says {found}, the source says {value}"
