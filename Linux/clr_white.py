"""
clr_white.py — normalize whitespace (spaces/tabs) while preserving newlines

Usage:
    python3 clr_white.py <text>

Example:
    python3 clr_white.py $'hello\t\tworld\nthis   is\tok'
    → 'hello world\nthis is ok'
"""

import argparse
import re


def clr_white(text: str) -> str:
    """Replace runs of spaces/tabs with a single space; newlines untouched."""
    return re.sub(r'[ \t]+', ' ', text)


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Normalize spaces and tabs while preserving newlines.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'Example:\n'
            "  python3 clr_white.py $'hello\\t\\tworld\\nthis   is\\tok'"
        )
    )
    parser.add_argument('text', help='Text to normalize')
    args = parser.parse_args()
    print(clr_white(args.text))


if __name__ == '__main__':
    main()
