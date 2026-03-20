"""
is_phone.py — validate simple German phone numbers

Accepted formats:
    +49 171 1234567
    0049 171 1234567
    0171-1234567
    0711 123456

Rejected:
    letters, double plus, trunk 0 after +49, too short

Usage:
    python3 is_phone.py <number> [<number> ...]

Example:
    python3 is_phone.py '+49 171 1234567' '0171-1234567' 'bad@@x'
"""

import argparse
import re

PHONE_PAT = re.compile(
    r"""
    ^
    (?:
        (?:\+|00)49[ -]?   # international: +49 or 0049
        [1-9][0-9]{1,4}[ -]?       # prefix, no trunk 0
        [0-9]{6,8}                 # subscriber
      |
        0[0-9]{1,4}[ -]?           # national: trunk 0 + prefix
        [0-9]{6,8}                 # subscriber
    )
    $
    """,
    re.VERBOSE
)


def is_phone(s: str) -> bool:
    """Return True if s matches a recognised German phone number format."""
    return PHONE_PAT.fullmatch(s) is not None


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Validate German phone numbers.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'Example:\n'
            "  python3 is_phone.py '+49 171 1234567' '0171-1234567' 'bad'"
        )
    )
    parser.add_argument('numbers', nargs='+', help='Phone number(s) to validate')
    args = parser.parse_args()
    for number in args.numbers:
        status = 'PASS' if is_phone(number) else 'FAIL'
        print(f'{status}  {number}')


if __name__ == '__main__':
    main()
