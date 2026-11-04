"""CLI entry point: uv run python -m rubin_qa [survey] [page_size | oid oid ...]

  survey: ztf (default) | lsst | antares
"""

import argparse
import ast
import collections
import sys

import pandas as pd

from .config import (
    CONFIRM_THRESHOLD_SECONDS,
    DEFAULT_SURVEY,
    DEFAULT_PAGE_SIZE,
    ERROR_PREFIX,
    REPORTS_DIR,
    SURVEYS,
    WARN_PREFIX,
    mjd_to_datetime,
    now_mjd,
    utc_and_age,
)
from .reporting import deadline_for, estimate_runtime, run_antares_pipeline, run_pipeline

SUMMARY_COLUMNS = ["oid", "ndet", "top_class", "consensus", "n_classifiers", "status"]
# Exit status of a run its deadline stopped: the partial CSV is written, and the status
# says it is partial, so a unit shows failed instead of passing for a full report.
# 1 means no report was written; 2 is argparse's.
EXIT_DEADLINE = 3


def run_label(survey: str) -> tuple[str, str]:
    """
    How a run names itself: (label, slug), e.g. ("ALeRCE ZTF", "alerce_ztf") or
    ("ANTARES", "antares"). The header, the report heading, the summary line, every
    error and the CSV file name all take it from here, so they cannot drift apart.
    """
    if survey == "antares":
        return "ANTARES", "antares"
    return f"ALeRCE {survey.upper()}", f"alerce_{survey}"


def sample_line(df: pd.DataFrame) -> str | None:
    """
    What an ANTARES run on the listing covered (df.attrs["sample"], set by
    run_antares_pipeline), so a reader sees how few exposures the loci came from.
    None for a run on given IDs or an ALeRCE run.
    """
    s = df.attrs.get("sample")
    if not s:
        return None
    span = ""
    if s["newest_mjd"]:
        lo, hi = s["newest_mjd"]
        span = f" (MJD {lo:.4f})" if lo == hi else f" (MJD {lo:.4f}–{hi:.4f})"
    times = "1 distinct time" if s["newest_times"] == 1 else f"{s['newest_times']} distinct times"
    by = s.get("surveys", {})
    split = f"ZTF {by.get('ZTF', 0)} / LSST {by.get('LSST', 0)}" + (
        f" / unknown {by['unknown']}" if by.get("unknown") else "")
    return (f"sample: {s['checked']} loci checked of {s['listed']} unique listed "
            f"({s['asked']} asked; the most recently updated), newest alerts at {times}{span}, "
            f"by survey {split}")


def newest_line(df: pd.DataFrame, survey: str, run_mjd: float) -> str:
    """
    The newest detection per survey among the objects this run checked, e.g. "newest ZTF
    of the 229 checked: 2026-11-03 13:22 UTC (17.5 h)", aged from the run's start. One
    wording for every survey and both brokers: the checked objects are a sample, so no
    figure here is claimed to be a broker's newest. An ALeRCE run names its own survey,
    an ANTARES run both; ANTARES detections of no known survey are counted, never dropped.
    Read from the report's columns only (newest_<survey>_mjd, unknown_survey_dets), so
    the CSV reproduces it.
    """
    n = len(df)
    parts = []
    for s in (("ZTF", "LSST") if survey == "antares" else (survey.upper(),)):
        mjds = pd.to_numeric(df[f"newest_{s.lower()}_mjd"], errors="coerce").dropna()
        parts.append(f"newest {s} of the {n} checked: "
                     + (utc_and_age(float(mjds.max()), run_mjd) if not mjds.empty else "none"))
    unknown = pd.to_numeric(df["unknown_survey_dets"], errors="coerce").fillna(0)
    if unknown.sum():
        parts.append(f"detections of no known survey: {int(unknown.sum())} in {int((unknown > 0).sum())} "
                     f"of the {n} checked")
    return "; ".join(parts)


def flag_split(df: pd.DataFrame) -> str:
    """
    "flagged=196/196 (insufficient_classifiers 196, ndet_lt_2 115)": the flagged rows, and
    how many rows carry each key of flag_reasons (a row with several counts under each),
    by count, then key. A report read back from its CSV holds each list as its text,
    "['ndet_lt_2']", which is parsed, so the CSV reproduces the line.
    """
    def keys_of(cell) -> list:
        if isinstance(cell, str):
            return ast.literal_eval(cell)
        return cell if isinstance(cell, list) else []

    counts = collections.Counter(k for cell in df["flag_reasons"] for k in keys_of(cell))
    split = ", ".join(f"{k} {n}" for k, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
    return f"flagged={df['flag'].notna().sum()}/{len(df)}" + (f" ({split})" if split else "")


def _planned_count(rest: list) -> int:
    """How many objects the run will touch: an explicit ID list, or a page size."""
    if rest and not rest[0].isdigit():
        return len(rest)
    return int(rest[0]) if rest else DEFAULT_PAGE_SIZE


def _confirm_long_run(survey: str, rest: list, assume_yes: bool) -> None:
    """
    Ask before starting a long scan. Exits 1 if the operator declines.

    Only prompts on a TTY: the systemd timers run with no stdin, and a pipeline
    that blocks on input there would hang a unit until its timeout rather than
    produce a report. Non-interactive runs log the estimate and proceed — the
    estimate is the useful part in a service log either way.
    """
    label, _ = run_label(survey)
    n = _planned_count(rest)
    estimate = estimate_runtime(n, survey)
    if estimate <= CONFIRM_THRESHOLD_SECONDS:
        return

    summary = (
        f"{label}: {n} objects ≈ {estimate / 60:.0f} min estimated "
        f"(deadline {deadline_for(n, survey) / 60:.0f} min)"
    )

    # sys.stdin is None when fd 0 is closed outright (StandardInput=close, some
    # embeddings) — reaching for .isatty() there would crash the run rather than
    # skip a prompt. systemd's default of StandardInput=null gives a real file
    # object, so the units in systemd/ take the isatty() path.
    interactive = sys.stdin is not None and sys.stdin.isatty()
    if assume_yes or not interactive:
        print(f"{WARN_PREFIX}{summary} — proceeding", file=sys.stderr, flush=True)
        return

    try:
        reply = input(f"{summary}. Continue? [y/N] ").strip().lower()
    except EOFError:
        reply = ""
    if reply not in ("y", "yes"):
        print(f"{ERROR_PREFIX}{label}: aborted — no report written", file=sys.stderr)
        sys.exit(1)


def _dispatch(survey: str, rest: list, quiet: bool) -> pd.DataFrame:
    """Route to the right pipeline. A numeric first target is a page size."""
    label, _ = run_label(survey)
    header = f"=== {label} Alert Data Quality Pipeline  {{}} ===\n"
    if survey == "antares":
        if rest and not rest[0].isdigit():
            if not quiet:
                print(header.format(f"locus_ids={rest}"))
            return run_antares_pipeline(locus_ids=rest, quiet=quiet)
        n = int(rest[0]) if rest else DEFAULT_PAGE_SIZE
        if not quiet:
            print(header.format(f"page_size={n}"))
        return run_antares_pipeline(page_size=n, quiet=quiet)

    if rest and not rest[0].isdigit():
        if not quiet:
            print(header.format(f"oids={rest}"))
        return run_pipeline(survey=survey, oids=rest, quiet=quiet)

    n = int(rest[0]) if rest else DEFAULT_PAGE_SIZE
    if not quiet:
        print(header.format(f"page_size={n}"))
    return run_pipeline(page_size=n, survey=survey, quiet=quiet)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="LSST/ZTF/ANTARES Alert Data Quality Pipeline"
    )
    # choices= is what stops a bare page size being read as a survey name:
    # `pipeline.py 50` otherwise parsed 50 as the survey, ran with the default
    # page size, and wrote a qa_50_*.csv. Rejecting it costs one argparse line.
    parser.add_argument(
        "survey", nargs="?", default=DEFAULT_SURVEY, choices=SURVEYS,
        metavar="survey",
        help=f"Broker/survey: {' | '.join(SURVEYS)} (default: {DEFAULT_SURVEY})",
    )
    parser.add_argument(
        "targets", nargs="*",
        help="page_size (int) or explicit oid/locus_id list",
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true",
        help="One-line summary only: broker, totals, output path",
    )
    parser.add_argument(
        "-y", "--yes", action="store_true",
        help="Skip the confirmation prompt for long runs",
    )
    opts = parser.parse_args()

    survey = opts.survey
    rest   = opts.targets
    quiet  = opts.quiet
    label, slug = run_label(survey)

    _confirm_long_run(survey, rest, opts.yes)
    # The run's one reading of the clock, after any prompt and before the listing: the
    # file name shows it in local time (as the monitor names its report), run_utc in UTC
    # is the time of record, and every age in the summary is measured from it.
    run_mjd = now_mjd()
    started = mjd_to_datetime(run_mjd)

    try:
        df = _dispatch(survey, rest, quiet)
    except Exception as e:
        print(
            f"{ERROR_PREFIX}{label}: pipeline failed — {type(e).__name__}: {e}",
            file=sys.stderr,
        )
        sys.exit(1)

    if df.empty:
        # No rows means the fetch failed or returned nothing — writing here
        # would leave a header-only CSV that looks like a successful run.
        print(
            f"{ERROR_PREFIX}{label}: no objects processed — no report written",
            file=sys.stderr,
        )
        sys.exit(1)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = REPORTS_DIR / f"qa_{slug}_{started.astimezone():%Y%m%d_%H%M%S}_n{len(df)}.csv"
    df.insert(0, "run_utc", started.isoformat(timespec="seconds"))
    df.to_csv(csv_path, index=False)

    sample = sample_line(df)
    newest = newest_line(df, survey, run_mjd)
    if quiet:
        print(f"{label}  n={len(df)}  {flag_split(df)}  | {newest}  → {csv_path}"
              + (f"  | {sample}" if sample else ""))
    else:
        print(f"\n=== {label} Data Quality Report ===")
        if sample:
            print(sample)
        print(df[SUMMARY_COLUMNS].to_string())
        print(f"\n{newest}")
        print(f"{flag_split(df)}  |  full report: {csv_path}")

    stopped = df.attrs.get("stopped_at_deadline")
    if stopped:
        print(
            f"{ERROR_PREFIX}{label}: stopped at the deadline after {stopped['done']}/{stopped['of']} objects "
            f"({stopped['elapsed']:.0f}s elapsed, deadline {stopped['deadline']:.0f}s); "
            f"partial report written: {csv_path}",
            file=sys.stderr,
        )
        sys.exit(EXIT_DEADLINE)


if __name__ == "__main__":
    main()
