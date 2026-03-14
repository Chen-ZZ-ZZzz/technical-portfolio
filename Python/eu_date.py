"""
eu_date.py — rename files with American-style dates (MM-DD-YYYY)
             to European-style (DD-MM-YYYY) recursively

Usage:
    python3 eu_date.py <directory>

Example:
    python3 eu_date.py ./reports
    → spam12-31-1900.txt becomes spam31-12-1900.txt
"""

import argparse
import re
from pathlib import Path

date_pat = re.compile(r'(?P<month>\d{2})-(?P<day>\d{2})-(?P<year>\d{4})', re.A)


def swap_date(match: re.Match) -> str:
    return f'{match.group("day")}-{match.group("month")}-{match.group("year")}'


def convert_dates(root: Path) -> None:
    count = 0
    for folder, _, fnames in root.walk(top_down=False):
        for fn in fnames:
            new_name = date_pat.sub(swap_date, fn)
            if new_name != fn:
                old_path = Path(folder) / fn
                old_path.rename(old_path.with_name(new_name))
                print(f'Renamed: {fn} → {new_name}')
                count += 1
    print(f'Done — {count} file(s) renamed.')


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Rename American-style dates (MM-DD-YYYY) to European-style (DD-MM-YYYY).',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'Example:\n'
            '  python3 eu_date.py ./reports'
        )
    )
    parser.add_argument('directory', type=Path, help='Directory to process recursively')
    args = parser.parse_args()

    if not args.directory.is_dir():
        parser.error(f"Directory not found: '{args.directory}'")

    convert_dates(args.directory)


if __name__ == '__main__':
    main()
