#!/usr/bin/env python3
"""
clr_white.py -- normalize whitespace (spaces/tabs) while preserving newlines

Usage:
    python3 clr_white.py <file>         # print cleaned output to stdout
    python3 clr_white.py <file> -i      # overwrite file in place
    echo "hello    world" | python3 clr_white.py   # read from stdin

"""

import argparse
import re
import sys


def clr_white(text: str) -> str:
    """Replace runs of spaces/tabs with a single space; newlines untouched."""
    return re.sub(r"[ \t]+", " ", text)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Normalize spaces and tabs while preserving newlines.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 clr_white.py notes.txt\n"
            "  python3 clr_white.py notes.txt -i\n"
            "  echo 'hello    world' | python3 clr_white.py"
        )
    )
    parser.add_argument("file", nargs="?", help="File to process (reads stdin if omitted)")
    parser.add_argument("--in-place", "-i", action="store_true", help="Overwrite file in place")
    args = parser.parse_args()

    if args.in_place and not args.file:
        parser.error("--in-place requires a file argument")

    if args.file:
        text = open(args.file, "r", encoding="utf-8").read()
    else:
        text = sys.stdin.read()

    result = clr_white(text)

    if args.in_place:
        with open(args.file, "w", encoding="utf-8") as f:
            f.write(result)
    else:
        print(result, end="")


if __name__ == "__main__":
    main()
