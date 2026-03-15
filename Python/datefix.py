"""
datefix.py — convert DD.MM.YYYY dates to ISO format YYYY-MM-DD in text

Leaves already-correct ISO dates untouched.
Uses lookarounds to avoid matching partial strings.

Usage:
    python3 datefix.py <text>

Example:
    python3 datefix.py 'Meeting on 08.01.2026 and follow-up 2026-01-15.'
    → 'Meeting on 2026-01-08 and follow-up 2026-01-15.'
"""

import argparse
import re

DATE_PAT = re.compile(
    r"(?<![A-Za-z0-9_-])(?P<day>\d{2})\.(?P<month>\d{2})\.(?P<year>\d{4})(?![A-Za-z0-9_-])",
    re.ASCII | re.MULTILINE
)


def datefix(txt: str) -> str:
    """Convert DD.MM.YYYY to YYYY-MM-DD; ISO dates and other formats untouched."""
    return DATE_PAT.sub(
        lambda m: f"{m.group('year')}-{m.group('month')}-{m.group('day')}",
        txt
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert DD.MM.YYYY dates to ISO format YYYY-MM-DD.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  python3 datefix.py 'Meeting on 08.01.2026 and follow-up 2026-01-15.'"
        )
    )
    parser.add_argument("text", help="Text containing dates to convert")
    args = parser.parse_args()
    print(datefix(args.text))


if __name__ == "__main__":
    main()
