"""
rpt_words.py — find repeated consecutive words (case-insensitive)

Usage:
    python3 rpt_words.py <text>

Example:
    python3 rpt_words.py 'This is is a test. That that is fine.'
    → [('is', 8), ('that', 22)]
"""

import argparse
import re


def rpt(txt: str) -> list[tuple[str, int]]:
    """Return list of (word, start_index) for each repeated consecutive word."""
    out = []
    prev = None
    for m in re.finditer(r'\w+', txt, re.IGNORECASE):
        word = m.group(0).lower()
        if word == prev:
            out.append((word, m.start()))
        prev = word
    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Find repeated consecutive words in text.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'Example:\n'
            "  python3 rpt_words.py 'This is is a test. That that is fine.'"
        )
    )
    parser.add_argument('text', help='Text to search for repeated words')
    args = parser.parse_args()
    print(rpt(args.text))


if __name__ == '__main__':
    main()
