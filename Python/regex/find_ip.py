"""
find_ip.py — extract and validate IPv4 addresses from text

Only keeps addresses where each octet is 0–255 with no leading zeros.

Usage:
    python3 find_ip.py <text> [<text> ...]

Example:
    python3 find_ip.py '10.0.0.1 999.1.1.1 192.168.01.1'
    → ['10.0.0.1']
"""

import argparse
import re

IP_PAT = re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', re.ASCII)


def _valid_octet(p: str) -> bool:
    """Return True if p is a valid octet: no leading zeros, value 0–255."""
    if p != '0' and p.startswith('0'):
        return False
    try:
        return 0 <= int(p) <= 255
    except ValueError:
        return False


def find_ip(text: str) -> list[str]:
    """Extract valid IPv4 addresses from text."""
    return [
        m.group(0)
        for m in IP_PAT.finditer(text)
        if all(_valid_octet(p) for p in m.group(0).split('.'))
    ]


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Extract and validate IPv4 addresses from text.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'Example:\n'
            "  python3 find_ip.py '10.0.0.1 999.1.1.1 192.168.01.1'"
        )
    )
    parser.add_argument('text', nargs='+', help='Text to search for IPv4 addresses')
    args = parser.parse_args()
    print(find_ip(' '.join(args.text)))


if __name__ == '__main__':
    main()
