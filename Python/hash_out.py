"""
hash_out.py — extract hashtags from text, ignoring C# or mid-word # signs

Usage:
    python3 hash_out.py <text>

Example:
    python3 hash_out.py 'We love #python, but not C# or abc#def. Also #привет is ok.'
    → ['python', 'привет']
"""

import argparse
import re

HASHTAG_PAT = re.compile(r'(?<!\w)#(\w+)')


def hash_out(s: str) -> list[str]:
    """Return list of hashtag words, excluding false positives like C# or abc#def."""
    return HASHTAG_PAT.findall(s)


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Extract hashtags from text.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'Example:\n'
            "  python3 hash_out.py 'We love #python, but not C# or abc#def.'"
        )
    )
    parser.add_argument('text', help='Text to search for hashtags')
    args = parser.parse_args()
    print(hash_out(args.text))


if __name__ == '__main__':
    main()
