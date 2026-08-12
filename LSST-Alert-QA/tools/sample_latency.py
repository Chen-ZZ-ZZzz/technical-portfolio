"""
Collect one broker-latency sample and append it to logs/latency_samples.jsonl.

Sampling must be spread across the clock: the 4-5× swing seen between 2026-08-06 and
2026-08-11 could be time-of-day, day-of-week, or load, and samples clustered at one
hour cannot tell them apart. The hourly systemd timer in systemd/ does this with
RandomizedDelaySec, landing one sample inside each hour; running it by hand at odd
moments works too and mixes in fine.

Each run appends exactly one JSON line: timestamp, local hour, per-object cost per
survey, the candidate-fetch cost, and the SECONDS_PER_OBJECT values in force at the
time (so a later reading stays honest about what was being compared against).

Usage:
    python tools/sample_latency.py              # take a sample, append it (~2.5 min)
    python tools/sample_latency.py --quick      # skip the candidate fetch (~1.5 min)
    python tools/sample_latency.py --report     # read the log back, summarize
    python tools/sample_latency.py --report --by-hour

You do not need to run this often. Each run measures `--objects` objects per survey
and every one of them is kept, so --report pools measurements rather than counting
runs. The headline question — is ztf ~3.7s or the ~17s measured on 2026-08-06? — is
a 4-5x effect that should settle within about 5 runs. Only the hour-of-day breakdown
genuinely wants many; skip it if you do not care why the number moved, only what it is.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import statistics
import sys
import time
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from bench_latency import (  # noqa: E402
    ALL_TARGETS,
    bench_antares_objects,
    bench_candidates,
    bench_objects,
)

LOG_PATH = pathlib.Path(__file__).resolve().parents[1] / "logs" / "latency_samples.jsonl"
SAMPLE_OBJECTS = 5          # per survey; keeps a manual run to ~2.5 min
CANDIDATE_PAGE_SIZE = 100   # the size the daily runs actually use
CANDIDATE_REPEATS = 1       # one shot per sample; the spread comes from many samples
MIN_USEFUL_RUNS = 5         # a 4-5x effect needs few runs; hour-of-day needs many more
BOOT_WINDOW_SECONDS = 600   # a sample this soon after boot is probably a timer catch-up


def _uptime_seconds() -> float | None:
    """Seconds since boot, or None if unavailable.

    Logged with every sample so a timer catch-up (which fires at boot, and so lands
    at whatever hour the machine came up) can be told apart from an on-schedule run
    during analysis. That keeps Persistent=true a safe setting: the potential bias
    is filterable after the fact instead of having to be prevented in the unit.
    """
    try:
        with open("/proc/uptime") as fh:
            return round(float(fh.read().split()[0]), 1)
    except (OSError, ValueError, IndexError):
        return None


def take_sample(n_objects: int, with_candidates: bool) -> dict:
    """Run one measurement pass and return the record to be logged."""
    from rubin_qa.config import INTER_OBJECT_DELAY, SECONDS_PER_OBJECT

    now = time.localtime()
    record: dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", now),
        "hour": now.tm_hour,
        "weekday": time.strftime("%a", now),
        "n_objects": n_objects,
        # systemd sets INVOCATION_ID; its absence means someone ran this by hand.
        "source": "timer" if os.environ.get("INVOCATION_ID") else "manual",
        "uptime_s": _uptime_seconds(),
        "per_object": {},
        "constants": dict(SECONDS_PER_OBJECT),
    }

    for survey in ALL_TARGETS:
        print(f"  sampling {survey} ...", file=sys.stderr, flush=True)
        try:
            rows = (
                bench_antares_objects(n_objects)
                if survey == "antares"
                else bench_objects(survey, n_objects)
            )
        except Exception as e:  # noqa: BLE001
            # A broker being unreachable is the single most valuable sample there
            # is — it is the episode the ceilings exist for. Record it and carry
            # on, rather than letting the timer unit die and lose the run.
            print(f"  {survey} FAILED: {e}", file=sys.stderr, flush=True)
            record["per_object"][survey] = {"error": f"{type(e).__name__}: {e}"}
            continue
        for row in rows:
            if row.get("kind") == "per_object":
                # Log the comparable figure: measured call time + the delay the
                # pipeline adds between objects, which is what SECONDS_PER_OBJECT means.
                record["per_object"][survey] = {
                    "median": row.get("median"),
                    "n_err": row["n_err"],
                    # Every individual object timing, so --report can pool across
                    # runs. One run already yields `n_objects` measurements per
                    # survey — summarizing them away would make sample size depend
                    # on how often someone remembers to run this.
                    "raw": row.get("raw", []),
                    "with_delay": (
                        round(row["median"] + INTER_OBJECT_DELAY, 2)
                        if row.get("median") is not None
                        else None
                    ),
                }

    if with_candidates:
        print("  sampling candidate fetch ...", file=sys.stderr, flush=True)
        try:
            record["candidates"] = {
                row["survey"]: row.get("median")
                for row in bench_candidates(
                    (CANDIDATE_PAGE_SIZE,), repeats=CANDIDATE_REPEATS
                )
            }
            record["candidate_page_size"] = CANDIDATE_PAGE_SIZE
        except Exception as e:  # noqa: BLE001
            print(f"  candidate fetch FAILED: {e}", file=sys.stderr, flush=True)
            record["candidates_error"] = f"{type(e).__name__}: {e}"

    return record


def summary_line(record: dict) -> str:
    """One-line digest for unattended runs — the journal does not need the JSON."""
    parts = []
    for survey in ALL_TARGETS:
        entry = record.get("per_object", {}).get(survey, {})
        if "error" in entry:
            parts.append(f"{survey}=ERR")
        elif entry.get("with_delay") is not None:
            parts.append(f"{survey}={entry['with_delay']:.2f}s")
    cand = record.get("candidates") or {}
    if cand:
        rendered = ",".join(
            f"{s}={v:.1f}" for s, v in cand.items() if v is not None
        )
        parts.append(f"candidates[{rendered}]")
    return f"{record['ts']}  " + "  ".join(parts)


def append_sample(record: dict, path: pathlib.Path) -> int:
    """Append one JSON line. Returns the total number of samples in the file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(record) + "\n")
    return sum(1 for _ in path.open())


def load_samples(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    for line_no, line in enumerate(path.read_text().splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            print(f"WARN: {path}:{line_no} is not valid JSON, skipped", file=sys.stderr)
    return records


def _inter_object_delay() -> float:
    from rubin_qa.config import INTER_OBJECT_DELAY

    return INTER_OBJECT_DELAY


def _spread(values: list[float]) -> str:
    if not values:
        return "no data"
    lo, hi = min(values), max(values)
    ratio = f", {hi / lo:.1f}x spread" if lo > 0 else ""
    return (
        f"n={len(values):2d}  median {statistics.median(values):5.2f}s  "
        f"range {lo:.2f}-{hi:.2f}s{ratio}"
    )


def _is_boot_adjacent(record: dict) -> bool:
    uptime = record.get("uptime_s")
    return uptime is not None and uptime < BOOT_WINDOW_SECONDS


def report(path: pathlib.Path, by_hour: bool, exclude_boot: bool) -> None:
    records = load_samples(path)
    if not records:
        print(f"No samples yet in {path}. Run without --report to take one.")
        return

    boot_adjacent = [r for r in records if _is_boot_adjacent(r)]
    if exclude_boot:
        records = [r for r in records if not _is_boot_adjacent(r)]
        if not records:
            print("Every sample was boot-adjacent; nothing left after --exclude-boot.")
            return

    plural = "" if len(records) == 1 else "s"
    print(f"{len(records)} sample{plural} in {path}")
    print(f"  first: {records[0]['ts']}")
    print(f"  last:  {records[-1]['ts']}")
    hours = sorted({r["hour"] for r in records})
    print(f"  hours covered: {', '.join(f'{h:02d}' for h in hours)}")

    by_source: dict[str, int] = {}
    for record in records:
        by_source[record.get("source", "unknown")] = (
            by_source.get(record.get("source", "unknown"), 0) + 1
        )
    print(f"  by source: {', '.join(f'{k}={v}' for k, v in sorted(by_source.items()))}")

    if boot_adjacent:
        # With Persistent=true a catch-up run lands at boot time, not a random hour.
        # Flag it so the by-hour view is read with that in mind; --exclude-boot drops them.
        note = "excluded" if exclude_boot else "included — see --exclude-boot"
        print(
            f"  boot-adjacent (<{BOOT_WINDOW_SECONDS}s uptime): "
            f"{len(boot_adjacent)}, {note}"
        )

    print("\nPer-object cost, incl. INTER_OBJECT_DELAY (what SECONDS_PER_OBJECT should be):")
    print(f"  pooling every object measured across all {len(records)} run{plural}")
    current = records[-1].get("constants", {})
    delay = _inter_object_delay()
    for survey in ALL_TARGETS:
        # Pool raw timings from every run. Older records predating `raw` fall back
        # to their per-run median so early samples still count for something.
        values: list[float] = []
        for record in records:
            entry = record.get("per_object", {}).get(survey, {})
            if entry.get("raw"):
                values += [round(t + delay, 2) for t in entry["raw"]]
            elif entry.get("with_delay") is not None:
                values.append(entry["with_delay"])
        line = f"  {survey:8s} {_spread(values)}"
        if values and survey in current:
            line += f"   | config says {current[survey]}s"
        print(line)

    # Failures are the point of sampling unattended: they are the episodes the
    # ceilings exist for, and they carry no timing, so they vanish from the stats
    # above. Surface them separately or they go unnoticed.
    failures = [
        (record["ts"], survey, entry["error"])
        for record in records
        for survey, entry in record.get("per_object", {}).items()
        if "error" in entry
    ]
    if failures:
        print(f"\nFailed fetches ({len(failures)} across {len(records)} runs):")
        for ts, survey, error in failures[-5:]:
            print(f"  {ts}  {survey:8s} {error[:96]}")
    else:
        print("\nNo failed fetches recorded.")

    cand = [r for r in records if r.get("candidates")]
    if cand:
        print(f"\nCandidate fetch (page_size={cand[-1].get('candidate_page_size')}):")
        for survey in ALL_TARGETS:
            values = [
                r["candidates"][survey] for r in cand
                if r["candidates"].get(survey) is not None
            ]
            print(f"  {survey:8s} {_spread(values)}")

    if by_hour:
        print("\nBy hour of day (ztf per-object, incl. delay):")
        for hour in hours:
            values = []
            for record in records:
                if record["hour"] != hour:
                    continue
                entry = record.get("per_object", {}).get("ztf", {})
                if entry.get("raw"):
                    values += [round(t + delay, 2) for t in entry["raw"]]
                elif entry.get("with_delay") is not None:
                    values.append(entry["with_delay"])
            if values:
                runs = sum(1 for r in records if r["hour"] == hour)
                print(f"  {hour:02d}:00  {_spread(values)}  ({runs} run{'' if runs == 1 else 's'})")
        print(
            "  NOTE: hour-of-day is the underpowered question — pooling helps the "
            "headline number,\n        but each hour bucket still needs its own runs."
        )

    if len(records) < MIN_USEFUL_RUNS:
        print(
            f"\n{len(records)} run{plural}. The headline question (is ztf ~3.7s or "
            f"~17s?) is a 4-5x effect\nand should settle by ~{MIN_USEFUL_RUNS} runs "
            "spread across different hours."
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect or review broker-latency samples (run manually, at odd hours)."
    )
    parser.add_argument("--report", action="store_true", help="Summarize collected samples.")
    parser.add_argument("--by-hour", action="store_true", help="With --report: break down by hour.")
    parser.add_argument(
        "--exclude-boot", action="store_true",
        help="With --report: drop samples taken just after boot (timer catch-up runs), "
             "which land at boot time rather than a random hour.",
    )
    parser.add_argument("--quick", action="store_true", help="Skip the candidate fetch.")
    parser.add_argument(
        "--objects", type=int, default=SAMPLE_OBJECTS,
        help=f"Objects per survey (default: {SAMPLE_OBJECTS}).",
    )
    parser.add_argument("--log", type=pathlib.Path, default=LOG_PATH, help="JSONL log path.")
    parser.add_argument("--dry-run", action="store_true", help="Print the sample, do not append.")
    parser.add_argument(
        "--quiet", action="store_true",
        help="Print a one-line summary instead of the full JSON (use from a timer).",
    )
    args = parser.parse_args()

    if args.report:
        report(args.log, args.by_hour, args.exclude_boot)
        return

    if not args.quiet:
        print(f"Taking a latency sample ({args.objects} objects/survey) ...", file=sys.stderr)
    record = take_sample(args.objects, with_candidates=not args.quick)

    if args.dry_run:
        print(json.dumps(record, indent=2))
        print("\n--dry-run: not appended", file=sys.stderr)
        return

    total = append_sample(record, args.log)
    if args.quiet:
        print(f"{summary_line(record)}  [{total} samples]")
    else:
        print(json.dumps(record, indent=2))
        print(f"\nappended to {args.log} ({total} samples so far)", file=sys.stderr)


if __name__ == "__main__":
    main()
