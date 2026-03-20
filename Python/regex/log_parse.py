"""
log_parse.py — parse structured log lines into field dictionaries

Expected format:
    2026-01-08 10:15:03 INFO auth user=alice ip=10.0.0.7

Usage:
    python3 log_parse.py <logfile>

Example:
    python3 log_parse.py sample.log
"""

import argparse
import re
from pathlib import Path

LOG_PAT = re.compile(
    r"""
    ^
    (?P<date>\d{4}-\d{2}-\d{2})\s+
    (?P<time>\d{2}:\d{2}:\d{2})\s+
    (?P<level>[A-Z]+)\s+
    (?P<module>[A-Za-z0-9_.-]{2,})\s+
    user=(?P<user>\w+)\s+
    ip=(?P<ip>(?:\d{1,3}\.){3}\d{1,3})
    $
    """,
    re.VERBOSE | re.ASCII
)


def log_parse(line: str) -> dict | None:
    """Parse a log line into a dict of fields, or None if no match."""
    m = LOG_PAT.fullmatch(line.strip())
    return m.groupdict() if m else None


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Parse structured log lines into fields.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'Example:\n'
            '  python3 log_parse.py sample.log'
        )
    )
    parser.add_argument('logfile', type=Path, help='Log file to parse')
    args = parser.parse_args()

    if not args.logfile.is_file():
        parser.error(f"File not found: '{args.logfile}'")

    for line in args.logfile.read_text(encoding='utf-8').splitlines():
        result = log_parse(line)
        if result:
            print(result)


if __name__ == '__main__':
    main()
