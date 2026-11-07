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
start timeouts, write paths) against the files, its "frozen" against the files test_sso_frozen pins
(active and dormant cannot be read from a file: arming is the install), and the README's
antares-client version against the lock. It checks what the docs say about the units,
never that a unit works: that is the user's install.

Beside the table, test_every_doc_link_lands guards the links in and between the READMEs
and CLAUDE.md, the one kind of pointer a test can check in full: GitHub builds an anchor
from its heading's text, so renaming a heading breaks every link to it without a sound.
Links are not rows, because a row's pattern sees only the path spelling it expects:
README.md#x written inside systemd/README.md opens that same file, and would match none.

Adding the next guarded quote is one row; a new link needs none.
"""

import posixpath
import re
import tomllib
from pathlib import Path

import pytest

from rubin_qa import classifier, replay, sky_catalog, tns, transient_monitor
from rubin_qa.antares_api import ANTARES_MAX_PAGE_SIZE
from rubin_qa.config import ALERCE_MAX_PAGE_SIZE, DEFAULT_SECONDS_PER_OBJECT, SECONDS_PER_OBJECT
from tests.test_sso_frozen import FROZEN_FILES

ROOT = Path(__file__).resolve().parents[1]
SYSTEMD = ROOT / "systemd"
DOCS = {name: (ROOT / name).read_text() for name in ("README.md", "systemd/README.md", "CLAUDE.md")}
# A relative link to a doc, to a heading in one, or to a heading in this one: (path, anchor).
# A URL is no pointer between the docs, nor is a link to a file that is not a doc.
DOC_LINK = re.compile(r"\]\((?!\w+://)([^)#\s]*\.md)?(?:#([^)\s]+))?\)")


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


def writes(service: str) -> str:
    """A unit's ReadWritePaths as the write-path table spells them: `dir/`, in the file's order."""
    return ", ".join(f"`{Path(p).name}/`" for p in unit(f"{service}.service.example", "ReadWritePaths").split())


def locked(package: str) -> str:
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    return next(p["version"] for p in lock["package"] if p["name"] == package)


def anchors(text: str) -> frozenset[str]:
    """The anchors GitHub builds from a doc's headings: lower case, punctuation dropped,
    each space a hyphen, a repeated heading suffixed -1, -2. A # line inside a code fence
    is not a heading."""
    made, seen, fenced = set(), {}, False
    for line in text.splitlines():
        fenced ^= line.startswith("```")
        heading = None if fenced else re.match(r"#{1,6} (.+)", line)
        if heading:
            slug = re.sub(r"[^\w\- ]", "", heading[1].strip().lower()).replace(" ", "-")
            made.add(f"{slug}-{seen[slug]}" if slug in seen else slug)
            seen[slug] = seen.get(slug, 0) + 1
    return frozenset(made)


def doc_links(text: str) -> list[tuple[str, str]]:
    """Every link in a doc that points to a doc or a heading; either part may be empty."""
    return [link for link in DOC_LINK.findall(text) if any(link)]


def broken_links(docs: dict[str, str]) -> list[str]:
    """The links that land nowhere. A path resolves from the doc it is written in, as GitHub
    resolves it. A doc outside `docs` counts as missing, so a new doc cannot go unchecked."""
    broken = []
    for name, text in docs.items():
        for path, anchor in doc_links(text):
            target = posixpath.normpath(posixpath.join(posixpath.dirname(name), path)) if path else name
            if target not in docs or (anchor and anchor not in anchors(docs[target])):
                broken.append(f"{name}: ({path}#{anchor})")
    return broken


TIMERS =sorted(p.stem for p in SYSTEMD.glob("*.timer"))
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
        ("seconds per object, antares", r"The constant for antares is now\s+\*\*([\d.]+)s\*\*", SECONDS_PER_OBJECT["antares"]),
        ("unmeasured-survey fallback", r"The ([\d.]+)s figure was an episode", DEFAULT_SECONDS_PER_OBJECT),
        # the TimeoutStartSec check: each unit's n and start timeout, read from its unit file
        *[(f"timeout check, {u}", rf"(?m)^\| `{u}` \| (\d+) \|.*\| `(\w+)` \|$",
           (runs(f"{u}.service.example").split()[2], unit(f"{u}.service.example", "TimeoutStartSec")))
          for u in ("lsst-pipeline-alerce", "lsst-pipeline-antares")],
        ("antares-client version", r"antares-client ([\d.]+) \(the pinned version\)", locked("antares-client")),
        ("ALeRCE LSST page limit", r"refuses `page_size` above (\d+)|N ≤ (\d+): the LSST listing's page limit",
         ALERCE_MAX_PAGE_SIZE),
        ("ANTARES listing page cap", r"so the cap is the client's own ceiling, (\d+)", ANTARES_MAX_PAGE_SIZE),
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
        # the write-path table: every unit not frozen has a row naming it (a row may name two)
        *[(f"write paths, {t}", rf"\| (?:`[\w-]+`, )*`{t}`(?:, `[\w-]+`)* \| ((?:`\w+/`(?:, )?)+) \|", writes(t))
          for t in TIMERS if not frozen(t)],
    ],
}


def quotes(pattern: str, text: str) -> list[tuple[str, ...]]:
    """Every quote the pattern finds: the groups of each match, whichever alternative matched."""
    return [tuple(g for g in m.groups() if g is not None) for m in re.finditer(pattern, text)]


def matches(quote: str, value) -> bool:
    """A number to within rounding, anything else exactly."""
    if isinstance(value, (int, float)):
        return float(quote) == pytest.approx(value)
    return quote == value


def says(quote: tuple[str, ...], value) -> bool:
    expected = value if isinstance(value, tuple) else (value,)
    return len(quote) == len(expected) and all(matches(q, v) for q, v in zip(quote, expected))


@pytest.mark.parametrize("doc,pattern,value", [
    pytest.param(doc, p, v, id=f"{doc}: {what}") for doc, rows in GUARDED.items() for what, p, v in rows])
def test_the_docs_quote_what_they_describe(doc, pattern, value):
    found = quotes(pattern, DOCS[doc])
    assert found, f"{doc} no longer quotes it ({pattern!r}): drop the row, or the quote was reworded"
    wrong = [q for q in found if not says(q, value)]
    assert not wrong, f"{doc} says {wrong}, the source says {value}"


def test_every_doc_link_lands():
    unlinked = [name for name, text in DOCS.items() if not doc_links(text)]
    assert not unlinked, f"no links seen in {unlinked}: nothing would be guarded"
    assert not broken_links(DOCS)


@pytest.mark.parametrize("docs", [
    pytest.param({"a.md": "# One\n[x](#two)\n"}, id="no such heading"),
    pytest.param({"a.md": "# Notes\n# Notes\n[x](#notes-2)\n"}, id="past the repeated headings"),
    pytest.param({"a.md": "# A\n[x](b.md#a)\n", "b.md": "# B\n"}, id="no such heading in the other doc"),
    pytest.param({"a.md": "[x](b.md)\n"}, id="no such doc"),
    pytest.param({"a.md": "[x](b.md#c)\n"}, id="no such doc, with an anchor"),
    pytest.param({"README.md": "# Quirks\n", "systemd/README.md": "# Logging\n[x](README.md#quirks)\n"},
                 id="path written as if from the root"),
    pytest.param({"a.md": "```\n# Fenced\n```\n[x](#fenced)\n"}, id="a # line in a fence is no heading"),
])
def test_a_link_that_lands_nowhere_is_reported(docs):
    assert broken_links(docs)


def test_a_link_that_lands_is_not_reported():
    docs = {
        "README.md": "# Known API Quirks\n[x](systemd/README.md#notes-1)\n[y](systemd/README.md)\n"
                     "[url](https://example.com/a.md#b)\n[file](reports/a.csv)\n",
        "systemd/README.md": "# Notes\n# Notes\n[x](../README.md#known-api-quirks)\n[y](#notes)\n",
    }
    assert not broken_links(docs)
