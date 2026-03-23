"""
log_scan.py -- scan log files for ERROR/WARN entries with timestamps
=====================================================================
Usage:
    python3 log_scan.py /path/to/logs
    python3 log_scan.py /path/to/single.log
    python3 log_scan.py /path/to/logs --level ERROR CRITICAL
    python3 log_scan.py /path/to/logs -o json
    python3 log_scan.py /path/to/logs -o csv > report.csv
    python3 log_scan.py /path/to/logs -o csv --save

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

from common import _red, _USE_COLOR, report_timestamp, save_report

DEFAULT_LEVELS = ("ERROR", "WARN")

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
    if not _USE_COLOR:
        return level
    code = _LEVEL_COLORS.get(level, "")
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
        f = path.open(errors="replace")
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


def _print_table(reports: list[FileReport], levels: tuple[str, ...],
                 single_file: bool = False, file=None, plain: bool = False) -> None:
    """Human-readable summary table. plain=True disables ANSI colors (for saving)."""
    p = lambda *a, **kw: print(*a, **kw, file=file)
    color_level = (lambda lv: lv) if plain else _colorize_level

    if not reports:
        p("No .log files found.")
        return

    p(f"Report: {report_timestamp()}\n")

    col = max(len(str(r.path)) for r in reports)
    header = f"{'FILE':<{col}}  " + "  ".join(f"{lv:>8}" for lv in levels)
    p(header)
    p("-" * len(header))

    totals: dict[str, int] = defaultdict(int)
    for r in reports:
        row = f"{str(r.path):<{col}}  " + "  ".join(
            f"{r.counts.get(lv, 0):>8}" for lv in levels
        )
        p(row)
        for lv in levels:
            totals[lv] += r.counts.get(lv, 0)

    p("-" * len(header))
    p(f"{'TOTAL':<{col}}  " + "  ".join(f"{totals[lv]:>8}" for lv in levels))

    if single_file:
        all_hits = [h for r in reports for h in r.hits]
        if all_hits:
            p(f"\n--- Matched lines ---")
            for h in all_hits:
                label = color_level(h.level)
                ts = f"[{h.timestamp}] " if h.timestamp else ""
                p(f"  {h.line_number:>5}: {ts}{label}: {h.line[:120]}")
    else:
        all_hits = [h for r in reports for h in r.hits if h.timestamp]
        if all_hits:
            recent = sorted(all_hits, key=lambda h: h.timestamp or "", reverse=True)[:5]
            p(f"\n--- Most recent entries (with timestamps) ---")
            for h in recent:
                label = color_level(h.level)
                p(f"  [{h.timestamp}] {label}: {h.line[:120]}")


def _format_json(reports: list[FileReport]) -> str:
    """JSON output for programmatic consumption."""
    data = {
        "timestamp": report_timestamp(),
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan log files for ERROR/WARN entries with timestamps.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 log_scan.py /path/to/logs\n"
            "  python3 log_scan.py app.log --level ERROR CRITICAL\n"
            "  python3 log_scan.py /path/to/logs -o json\n"
            "  python3 log_scan.py /path/to/logs -o csv > report.csv\n"
            "  python3 log_scan.py /path/to/logs -o csv --save"
        ),
    )
    parser.add_argument(
        "target", type=Path, nargs="?", default=Path("."),
        help="Directory or single .log file to scan (default: current dir)",
    )
    parser.add_argument(
        "--level", "-l", nargs="+", default=list(DEFAULT_LEVELS), metavar="LEVEL",
        help=f"Log levels to match (default: {' '.join(DEFAULT_LEVELS)})",
    )
    parser.add_argument(
        "--output", "-o", choices=["table", "json", "csv"], default="table",
        help="Output format (default: table)",
    )
    parser.add_argument(
        "--save", "-s", action="store_true",
        help="Save report to reports/ with timestamped filename",
    )
    args = parser.parse_args()

    if not args.target.exists():
        print(f"Error: '{args.target}' does not exist.", file=sys.stderr)
        sys.exit(1)

    if args.target.is_file() and args.target.suffix != ".log":
        print(f"Warning: '{args.target}' is not a .log file.", file=sys.stderr)

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
        output = _format_json(reports)
    elif args.output == "csv":
        output = _format_csv(reports)
    else:
        single = args.target.is_file()
        _print_table(reports, levels, single_file=single)
        output = None

    if output is not None and not args.save:
        print(output, end="" if args.output == "csv" else "\n")

    if args.save:
        save_fmt = args.output if args.output != "table" else "json"
        if output is None or save_fmt != args.output:
            if save_fmt == "json":
                save_content = _format_json(reports)
            elif save_fmt == "csv":
                save_content = _format_csv(reports)
            else:
                buf = io.StringIO()
                _print_table(reports, levels, file=buf, plain=True)
                save_content = buf.getvalue()
        else:
            save_content = output
        saved = save_report(save_content, save_fmt, "log_scan")
        if saved:
            print(f"Report saved: {saved}", file=sys.stderr)


if __name__ == "__main__":
    main()
