"""
Measure live broker call latency, to re-derive SECONDS_PER_OBJECT in config.py.

Times each client call directly, bypassing the retry wrappers — we want raw
per-call latency, and a failure recorded as a failure rather than retried away.

The number that feeds SECONDS_PER_OBJECT is the PER-OBJECT row plus
INTER_OBJECT_DELAY. The candidate fetch is reported separately and deliberately
excluded: it is a one-off per run, not a per-object cost. It is also the slowest
call in the system and the reason REQUEST_TIMEOUT stays at 60s, so --candidates
is worth running whenever that ceiling is in question.

Usage:
    python tools/bench_latency.py                      # per-object, all surveys
    python tools/bench_latency.py --rounds 2            # repeat; check stability
    python tools/bench_latency.py --candidates         # candidate fetch only
    python tools/bench_latency.py --out bench.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from typing import Any, Callable

ALERCE_SURVEYS = ("ztf", "lsst")
ALL_TARGETS = ("ztf", "lsst", "antares")
OBJECT_CALLS = ("query_detections", "query_magstats", "query_probabilities")
DEFAULT_SAMPLES = 10
DEFAULT_PAGE_SIZES = (10, 100)
CANDIDATE_REPEATS = 4


def _timed(fn: Callable, *args: Any, **kwargs: Any) -> tuple[float, str | None]:
    """Run fn, returning (elapsed_seconds, error_string_or_None)."""
    start = time.perf_counter()
    try:
        fn(*args, **kwargs)
        err = None
    except Exception as e:  # noqa: BLE001 — any failure is a data point here
        err = f"{type(e).__name__}: {e}"
    return time.perf_counter() - start, err


def _summarize(
    label: str, samples: list[tuple[float, str | None]], **tags: Any
) -> dict:
    """Reduce timing samples to a stats row. `tags` (survey, kind, ...) are merged in
    so callers can select rows by field rather than by parsing the label."""
    ok = [t for t, e in samples if e is None]
    bad = [(round(t, 2), e) for t, e in samples if e is not None]
    row: dict[str, Any] = {"call": label, **tags, "n": len(samples), "n_err": len(bad)}
    if ok:
        row |= {
            "min": round(min(ok), 2),
            "median": round(statistics.median(ok), 2),
            "mean": round(statistics.fmean(ok), 2),
            "max": round(max(ok), 2),
        }
    if bad:
        row["errors"] = bad[:3]
    return row


def _alerce_client():
    from alerce.core import Alerce

    from rubin_qa.client import _force_session_timeout

    client = Alerce()
    _force_session_timeout(client)
    return client


def bench_objects(survey: str, samples: int) -> list[dict]:
    """Time the per-object calls for one ALeRCE survey."""
    client = _alerce_client()
    objects = client.query_objects(page_size=samples, survey=survey)
    oids = list(dict.fromkeys(str(o) for o in objects["oid"].tolist()))[:samples]

    per_call: dict[str, list[tuple[float, str | None]]] = {c: [] for c in OBJECT_CALLS}
    per_object: list[float] = []
    for oid in oids:
        total = 0.0
        for name in OBJECT_CALLS:
            elapsed, err = _timed(
                getattr(client, name), oid, format="pandas", survey=survey
            )
            per_call[name].append((elapsed, err))
            total += elapsed
        per_object.append(total)

    rows = [
        _summarize(f"{survey}:{name}", per_call[name], survey=survey, kind="call")
        for name in OBJECT_CALLS
    ]
    row = _summarize(
        f"{survey}:PER-OBJECT (3 calls, excl. INTER_OBJECT_DELAY)",
        [(t, None) for t in per_object],
        survey=survey,
        kind="per_object",
    )
    # Keep the individual timings, not just the summary: a caller pooling many
    # runs wants every measurement, and 5 objects thrown away per run is the
    # difference between a usable sample size and a useless one.
    row["raw"] = [round(t, 2) for t in per_object]
    rows.append(row)
    return rows


def bench_antares_objects(samples: int) -> list[dict]:
    """Time the per-locus fetch."""
    from antares_client import search

    ids = list(dict.fromkeys(search.get_random_locus_ids(samples)))[:samples]
    timings = [_timed(search.get_by_id, locus_id) for locus_id in ids]
    row = _summarize(
        "antares:PER-OBJECT (get_by_id, excl. INTER_OBJECT_DELAY)",
        timings,
        survey="antares",
        kind="per_object",
    )
    row["raw"] = [round(t, 2) for t, err in timings if err is None]
    return [row]


def bench_candidates(
    page_sizes: tuple[int, ...], repeats: int = CANDIDATE_REPEATS
) -> list[dict]:
    """Time the one-off candidate fetch — highly variable, and the closest call to
    REQUEST_TIMEOUT. Reported per page size because the relationship is not linear."""
    from antares_client import search

    client = _alerce_client()
    rows = []
    for survey in ALERCE_SURVEYS:
        for page_size in page_sizes:
            samples = [
                _timed(client.query_objects, page_size=page_size, survey=survey)
                for _ in range(repeats)
            ]
            rows.append(
                _summarize(
                    f"{survey}:query_objects", samples,
                    survey=survey, kind="candidates", page_size=page_size,
                )
            )
    for page_size in page_sizes:
        samples = [
            _timed(search.get_random_locus_ids, page_size) for _ in range(repeats)
        ]
        rows.append(
            _summarize(
                "antares:get_random_locus_ids", samples,
                survey="antares", kind="candidates", page_size=page_size,
            )
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure live broker latency to re-derive SECONDS_PER_OBJECT."
    )
    parser.add_argument(
        "--targets", nargs="+", default=list(ALL_TARGETS), choices=ALL_TARGETS,
        help="Surveys to benchmark (default: all).",
    )
    parser.add_argument(
        "--samples", type=int, default=DEFAULT_SAMPLES,
        help=f"Objects per survey per round (default: {DEFAULT_SAMPLES}).",
    )
    parser.add_argument(
        "--rounds", type=int, default=1,
        help="Repeat the whole run; rounds should agree closely if the link is stable.",
    )
    parser.add_argument(
        "--candidates", action="store_true",
        help="Benchmark the one-off candidate fetch instead of per-object calls.",
    )
    parser.add_argument(
        "--page-sizes", nargs="+", type=int, default=list(DEFAULT_PAGE_SIZES),
        help="Page sizes for --candidates.",
    )
    parser.add_argument("--out", help="Write JSON results here (default: stdout only).")
    args = parser.parse_args()

    result: dict[str, Any] = {
        "measured": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "rounds": [],
    }
    for round_index in range(args.rounds):
        rows: list[dict] = []
        if args.candidates:
            rows += bench_candidates(tuple(args.page_sizes))
        else:
            for target in args.targets:
                print(f"### round {round_index + 1}: {target}", file=sys.stderr, flush=True)
                if target == "antares":
                    rows += bench_antares_objects(args.samples)
                else:
                    rows += bench_objects(target, args.samples)
        result["rounds"].append(rows)

    text = json.dumps(result, indent=2)
    print(text)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(text)
        print(f"\nwrote {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
