"""
email_domains.py — extract domain names from a list of email addresses

Usage:
    python3 email_domains.py <email1> <email2> ...

Example:
    python3 email_domains.py a@b.com tom.smith@sub.example.co.uk bad@@x.com
    → ['b.com', 'sub.example.co.uk']
"""

import argparse
import re

EMAIL_PAT = re.compile(r'^[^@\s]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)$')


def extract_domains(emails: list[str]) -> list[str]:
    """Return a list of domains from valid email addresses, ignoring invalid ones."""
    return [m.group(1) for e in emails if (m := EMAIL_PAT.fullmatch(e))]


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Extract domain names from email addresses.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'Example:\n'
            '  python3 email_domains.py a@b.com tom.smith@sub.example.co.uk bad@@x.com'
        )
    )
    parser.add_argument('emails', nargs='+', help='One or more email addresses')
    args = parser.parse_args()
    print(extract_domains(args.emails))


if __name__ == '__main__':
    main()
