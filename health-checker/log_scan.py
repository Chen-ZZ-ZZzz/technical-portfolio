"""
log_scan.py -- scan log files for ERROR/WARN entries with timestamps
=====================================================================
Usage:
    python3 log_scan.py /path/to/logs
    python3 log_scan.py /path/to/single.log
    python3 log_scan.py /path/to/logs --level ERROR CRITICAL
    python3 log_scan.py /path/to/logs -o json > report.json
    python3 log_scan.py /path/to/logs -s csv

Recognized timestamp formats:
    2026-03-21 14:05:03                     (ISO datetime)
    2026-03-21T14:05:03                     (ISO with T separator)
    2026-03-21T14:05:03.123456+01:00        (high-precision rsyslog)
    21.03.2026 14:05:03                     (German locale DD.MM.YYYY)
    Mar 21 14:05:03                         (syslog BSD traditional)
"""

import argparse
import csv
import io
import json
import re
import sys

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from common import (
    _USE_COLOR,
    report_timestamp,
    save_report,
)

DEFAULT_LEVELS = ("ERROR", "WARN")

# TODO: re-add with SQLite
# # log levels that map to ERROR db status (vs WARN)
# _ERROR_LEVELS = frozenset(("ERROR", "CRITICAL", "FATAL"))

# Matches common log timestamps at the start of a line
TIMESTAMP_PAT = re.compile(
    r"""
    ^(?:
        (?P<iso>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:\d{2})?)
      | (?P<german>\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}:\d{2})
      | (?P<syslog>[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})
    )
    """,
    re.VERBOSE,
)

# Level -> ANSI color code
_LEVEL_COLORS = {
    "ERROR":    "\033[31m",   # red
    "CRITICAL": "\033[31m",
    "FATAL":    "\033[31m",
    "WARN":     "\033[33m",   # yellow
    "WARNING":  "\033[33m",
}
_RESET = "\033[0m"


def _colorize_level(level: str) -> str:
    """Return a color wrapped LEVEL if _USE_COLOR is set, else plain."""
    if not _USE_COLOR:
        return level
    code = _LEVEL_COLORS.get(level, "") # unknown level -> no color
    return f"{code}{level}{_RESET}" if code else level


def _build_level_pattern(levels: tuple[str, ...]) -> re.Pattern:
    """Build a word-boundary regex matching any of the given log levels."""
    escaped = "|".join(re.escape(lv) for lv in levels)
    return re.compile(rf"\b({escaped})\b")


# -- Data classes --


@dataclass
class LogHit:
    """One matched log line."""
    level: str
    line_number: int
    timestamp: str | None
    line: str


@dataclass
class FileReport:
    """Scan results for one file."""
    path: Path
    counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    hits: list[LogHit] = field(default_factory=list)


# -- Scanning --


def _extract_timestamp(line: str) -> str | None:
    """Extract a timestamp from the beginning of a log line, or None."""
    m = TIMESTAMP_PAT.match(line)
    if not m:
        return None
    return m.group("iso") or m.group("german") or m.group("syslog")


def scan_file(path: Path, pattern: re.Pattern) -> FileReport:
    """Scan one file for matching log levels. Returns a FileReport."""
    report = FileReport(path=path)
    try:
        f = path.open(encoding="utf-8", errors="replace")
    except PermissionError:
        print(f"[SKIPPED] {path} (permission denied)", file=sys.stderr)
        return report
    except OSError as e:
        print(f"[SKIPPED] {path} ({e})", file=sys.stderr)
        return report

    with f:
        for lineno, line in enumerate(f, start=1):
            if match := pattern.search(line):
                level = match.group(1)
                report.counts[level] += 1
                report.hits.append(LogHit(
                    level=level,
                    line_number=lineno,
                    timestamp=_extract_timestamp(line),
                    line=line.rstrip("\n"),
                ))
    return report


def scan_dir(base: Path, pattern: re.Pattern) -> list[FileReport]:
    """Scan all .log files under a directory recursively."""
    return [
        scan_file(p, pattern)
        for p in sorted(base.rglob("*.log"))
        if p.is_file()
    ]


# -- Output formatters --


def _display_text(h: LogHit) -> str:
    """Line text with the leading timestamp removed (display only)."""
    if h.timestamp:
        return h.line.removeprefix(h.timestamp).lstrip()
    return h.line


def _print_table(reports: list[FileReport], levels: tuple[str, ...]) -> None:
    """Human-readable summary table and hit listing to stdout."""
    print(f"Report: {report_timestamp()}\n")

    col = max(len(str(r.path)) for r in reports)
    header = f"{'FILE':<{col}}  " + "  ".join(f"{lv:>8}" for lv in levels)
    print(header)
    print("-" * len(header))

    totals: dict[str, int] = defaultdict(int)
    for r in reports:
        row = f"{str(r.path):<{col}}  " + "  ".join(
            f"{r.counts.get(lv, 0):>8}" for lv in levels
        )
        print(row)
        for lv in levels:
            totals[lv] += r.counts.get(lv, 0)

    print("-" * len(header))
    print(f"{'TOTAL':<{col}}  " + "  ".join(f"{totals[lv]:>8}" for lv in levels))

    for r in reports:
        if not r.hits:
            continue

        print(f"\n{r.path}")
        for h in r.hits:
            ts = f"[{h.timestamp}] " if h.timestamp else ""
            print(f"  {h.line_number:>8}: {ts}{_colorize_level(h.level)}: {_display_text(h)}")


def _format_json(reports: list[FileReport]) -> str:
    """JSON output for programmatic consumption."""
    data = {
        "report_timestamp": report_timestamp(),
        "files": [],
    }
    for r in reports:
        entry = {
            "file": str(r.path),
            "counts": dict(r.counts),
            "hits": [
                {
                    "level": h.level,
                    "line_number": h.line_number,
                    "timestamp": h.timestamp,
                    "line": h.line,
                }
                for h in r.hits
            ],
        }
        data["files"].append(entry)
    return json.dumps(data, indent=2)


def _format_csv(reports: list[FileReport]) -> str:
    """CSV output -- one row per hit for detailed analysis."""
    buf = io.StringIO()
    fieldnames = ["file", "line_number", "level", "timestamp", "line"]
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for r in reports:
        for h in r.hits:
            writer.writerow({
                "file": str(r.path),
                "line_number": h.line_number,
                "level": h.level,
                "timestamp": h.timestamp or "",
                "line": h.line,
            })
    return buf.getvalue()


# -- CLI --


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="scan log files for ERROR/WARN entries with timestamps.",
    )
    parser.add_argument(
        "target", type=Path, nargs="?", default=Path("."),
        help="directory or single .log file to scan (default: current dir)",
    )
    parser.add_argument(
        "--level", "-l", nargs="+", default=list(DEFAULT_LEVELS), metavar="LEVEL",
        help=f"log levels to match (default: {' '.join(DEFAULT_LEVELS)})",
    )
    parser.add_argument(
        "--output", "-o", choices=["table", "json", "csv"], default="table",
        help="output format (default: table)",
    )
    parser.add_argument(
        "--save", "-s", nargs="?", const="json", choices=["json", "csv"],
        default=None, metavar="FMT",
        help="save report to reports/ (json or csv; default json)",    )
    # TODO: re-add with SQLite
    # parser.add_argument(
    #     "--db", "-d", type=Path, default=None, metavar="PATH",
    #     help="SQLite database file to record results (default: no recording)",
    # )
    args = parser.parse_args(argv) # None -> uses sys.argv[1:]

    if not args.target.exists():
        parser.error(f"'{args.target}' does not exist.")
    if args.target.is_file() and args.target.suffix != ".log":
        print(f"Warning: '{args.target}' is not a .log file.", file=sys.stderr)
    return args


def main() -> None:
    args = _parse_args()
    levels = tuple(lv.upper() for lv in args.level)
    pattern = _build_level_pattern(levels)

    if args.target.is_file():
        reports = [scan_file(args.target, pattern)]
    else:
        reports = scan_dir(args.target, pattern)

    if not reports:
        print("No .log files found.")
        return

    if args.output == "json":
        print(_format_json(reports))
    elif args.output == "csv":
        print(_format_csv(reports), end="")
    else:
        _print_table(reports, levels)

    if args.save:
        content = _format_json(reports) if args.save == "json" else _format_csv(reports)
        saved = save_report(content, args.save, "log_scan")
        if saved:
            print(f"Report saved: {saved}")


if __name__ == "__main__":
    main()
