"""
Which ALeRCE classifier versions are live, checked against the classifier registry.

Run it when a QA report carries `unknown_classifier: name@version` (CLAUDE.md, "QA pipeline").
It reads the registry (rubin_qa.classifier: VOTERS, and CONTEXT in its three kinds) and never
writes it. It prints what should change: a name@version to move between VOTERS and CONTEXT,
or an unknown one to place. The registry is then edited by hand.

Method (2026-11-03; the findings: README, "Independent Evidence in ALeRCE's Classifiers"):
  - For each survey, the N newest objects with enough detections for the light-curve
    classifiers to have run (first detected within the lookback, at least MIN_NDET
    detections), and every row of their query_probabilities.
  - A version on at least one of them is live: ALeRCE classifies a new object only with what
    it runs today. A version seen only on older objects is retired: a registry voter on none of
    the newest, or an unknown pair from a QA report (--report) on none of them.
  - Context that is live by nature is no finding: a live version's hierarchy levels and a
    position label (HIERARCHY_LEVELS, POSITION_LABELS). A RETIRED pair on a newest object is:
    it is live again.
  - The registry records no survey, so a voter missing from the newest objects is judged only
    when every survey was sampled.

Blind spot: a live version that runs on few objects can be missed in a small sample. On ZTF,
BHRF and ATAT were on only 8 of the 20 newest objects (2026-11-03). A miss reports a live
version as retired; moved to context, it would no longer vote, so the result errs toward fewer
votes, the safe direction. A larger --sample narrows the blind spot.

Only newest_objects and probabilities touch ALeRCE: no cache, no state, no files written.
Exit 0 when the registry matches, 1 when it needs a change or a fetch failed.

Usage:
    uv run tools/alerce_live_classifiers.py
    uv run tools/alerce_live_classifiers.py --report reports/qa_alerce_ztf_<stamp>_n225.csv
    uv run tools/alerce_live_classifiers.py --surveys ztf --sample 50
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))  # rubin_qa without an install

from rubin_qa.classifier import HIERARCHY_LEVELS, POSITION_LABELS, RETIRED, VOTERS, _version_str  # noqa: E402
from rubin_qa.config import ALERCE_MAX_PAGE_SIZE, ERROR_PREFIX, WARN_PREFIX, from_root, now_mjd  # noqa: E402

SURVEYS = ("ztf", "lsst")
DEFAULT_SAMPLE = 20
MIN_NDET = 8             # detections: enough for the light-curve classifiers to have run (2026-11-03)
LOOKBACK_DAYS = 30.0     # days: how recent "newest" may be
ROWS_PER_OBJECT = 3      # the LSST listing repeats an object once per classifier (3 seen, 2026-11-03)
UNKNOWN_TOKEN = re.compile(r"unknown_classifier: ([^;]+)")

Pair = tuple[str, str]


@dataclass
class Verdict:
    live: Counter = field(default_factory=Counter)  # pair -> newest objects carrying it
    n_objects: int = 0
    changes: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def pairs_of(rows: list[dict]) -> set[Pair]:
    """The (name, version) pairs among one object's probability rows, versions as the registry spells them."""
    return {(str(r["classifier_name"]), _version_str(r["classifier_version"])) for r in rows}


def role(pair: Pair, voters: dict = VOTERS, retired: frozenset = RETIRED,
         live_context: frozenset = HIERARCHY_LEVELS | POSITION_LABELS) -> str:
    family = next((f for f, pairs in voters.items() if pair in pairs), None)
    if family:
        return f"vote: {family}"
    if pair in retired:
        return "retired"
    return "context, live by nature" if pair in live_context else "UNKNOWN"


def judge(newest: dict[str, list[dict]], elsewhere: Iterable[Pair] = (), every_survey: bool = True, *,
          voters: dict = VOTERS, retired: frozenset = RETIRED,
          live_context: frozenset = HIERARCHY_LEVELS | POSITION_LABELS) -> Verdict:
    """
    newest: each newest object's probability rows (key: any object id). elsewhere: pairs seen
    on other, older objects (a QA report's unknown_classifier flags). Returns the live pairs and
    the registry changes they call for; pure, no network.
    """
    v = Verdict(n_objects=len(newest))
    for rows in newest.values():
        v.live.update(pairs_of(rows))
    voter_pairs = {p for pairs in voters.values() for p in pairs}
    known = voter_pairs | retired | live_context
    on = lambda p: f"on {v.live[p]} of the {v.n_objects} newest objects"  # noqa: E731

    for p in sorted(v.live):
        if p not in known:
            v.changes.append(f"place {p[0]}@{p[1]}: live ({on(p)}), not in the registry. Add it to its "
                             "family in VOTERS, or to HIERARCHY_LEVELS / POSITION_LABELS if it is a level or a label")
        elif p in retired:
            v.changes.append(f"move {p[0]}@{p[1]} from RETIRED to VOTERS: live again ({on(p)})")
    if every_survey:
        for family, pairs in voters.items():
            for p in pairs:
                if p not in v.live:
                    v.changes.append(f"move {p[0]}@{p[1]} ({family}) from VOTERS to RETIRED: on none of the "
                                     f"{v.n_objects} newest objects (or the sample missed it: the blind spot)")
    else:
        v.notes.append("voters missing from the newest objects: not judged (needs every survey sampled)")
    for p in sorted(set(elsewhere) - set(v.live)):
        if p not in known:
            v.changes.append(f"add {p[0]}@{p[1]} to RETIRED: seen on older objects, on none of the "
                             f"{v.n_objects} newest")
    return v


def unknown_pairs(flags: Iterable[str | None]) -> set[Pair]:
    """The pairs named by a QA report's `unknown_classifier: name@version` flag tokens."""
    pairs = set()
    for flag in flags:
        for token in UNKNOWN_TOKEN.findall(flag or ""):
            name, _, version = token.strip().rpartition("@")
            pairs.add((name, version))
    return pairs


# ------------------------------------------------------------------ network (thin)


def newest_objects(survey: str, n: int, min_ndet: int, lookback_days: float) -> tuple[list[str], str | None]:
    """The survey's n newest object ids with at least min_ndet detections, newest first."""
    from rubin_qa.client import _api_call, _client

    count = "n_det" if survey == "lsst" else "ndet"
    res, err = _api_call(_client.query_objects, format="json", survey=survey,
                         firstmjd=[now_mjd() - lookback_days, 99999.0], **{count: [min_ndet, 10 ** 6]},
                         order_by="firstmjd", order_mode="DESC", page=1,
                         page_size=min(ALERCE_MAX_PAGE_SIZE, ROWS_PER_OBJECT * n))
    if err is not None:
        return [], err
    items = res.get("items", []) if isinstance(res, dict) else (res or [])
    return list(dict.fromkeys(str(i["oid"]) for i in items))[:n], None


def probabilities(oid: str, survey: str) -> tuple[list[dict], str | None]:
    from rubin_qa.client import _api_call, _client

    df, err = _api_call(_client.query_probabilities, oid, format="pandas", survey=survey)
    return ([] if df is None else df.to_dict("records")), err


# ------------------------------------------------------------------ CLI


def run(surveys: list[str], sample: int, min_ndet: int, lookback_days: float, report: pathlib.Path | None) -> int:
    failed = False
    newest: dict[str, list[dict]] = {}
    for survey in surveys:
        oids, err = newest_objects(survey, sample, min_ndet, lookback_days)
        if err is not None:
            print(f"{ERROR_PREFIX}{survey} newest objects: {err}", file=sys.stderr)
            failed = True
            continue
        mine = {}
        for oid in oids:
            rows, err = probabilities(oid, survey)
            if err is not None:
                print(f"{WARN_PREFIX}{survey} {oid}: probabilities failed ({err}), left out", file=sys.stderr)
                continue
            mine[f"{survey}:{oid}"] = rows
        newest.update(mine)
        live = judge(mine, every_survey=False).live
        print(f"\n{survey}: the {len(mine)} newest objects with >= {min_ndet} detections "
              f"(first detected within {lookback_days:g} d)")
        for p in sorted(live):
            print(f"  {p[0]}@{p[1]}: on {live[p]} of {len(mine)}  [{role(p)}]")

    elsewhere = set()
    if report is not None:
        import pandas as pd

        elsewhere = unknown_pairs(pd.read_csv(report)["flag"])
        print(f"\n{report.name}: {len(elsewhere)} unknown pair(s): "
              + (", ".join(f"{n}@{v}" for n, v in sorted(elsewhere)) or "none"))

    verdict = judge(newest, elsewhere, every_survey=set(surveys) == set(SURVEYS) and not failed)
    for note in verdict.notes:
        print(f"\nNote: {note}")
    if verdict.changes:
        print("\nThe registry (src/rubin_qa/classifier.py) needs these changes, made by hand:")
        for change in verdict.changes:
            print(f"  - {change}")
    elif not failed:
        print("\nThe registry matches: no change.")
    return 1 if failed or verdict.changes else 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--surveys", nargs="+", choices=SURVEYS, default=list(SURVEYS))
    p.add_argument("--sample", type=int, default=DEFAULT_SAMPLE, help=f"newest objects per survey (default {DEFAULT_SAMPLE})")
    p.add_argument("--min-ndet", type=int, default=MIN_NDET, help=f"detections at least (default {MIN_NDET})")
    p.add_argument("--lookback-days", type=float, default=LOOKBACK_DAYS)
    p.add_argument("--report", type=from_root, help="a QA report whose unknown_classifier flags to place "
                                                    "(relative to the repo root)")
    args = p.parse_args()
    return run(args.surveys, args.sample, args.min_ndet, args.lookback_days, args.report)


if __name__ == "__main__":
    sys.exit(main())
