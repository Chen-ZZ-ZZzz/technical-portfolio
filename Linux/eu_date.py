#!/usr/bin/env python3
"""
eu_date.py -- convert American-style dates (MM-DD-YYYY) to European-style (DD-MM-YYYY)

Accepts a directory or a file:
  directory -> rename filenames containing MM-DD-YYYY dates
  file      -> convert dates inside file content

Dry-run / stdout by default -- use --apply to write changes.

Usage:
    python3 eu_date.py <target> [-a]

Examples:
    python3 eu_date.py ./downloads           # dry run: show renames
    python3 eu_date.py ./downloads -a        # rename for real
    python3 eu_date.py notes.txt             # print converted text to stdout
    python3 eu_date.py notes.txt -a          # overwrite file in place
"""

import argparse
import os
import re
import tempfile
from pathlib import Path

DATE_PAT = re.compile(r"(?P<month>\d{2})-(?P<day>\d{2})-(?P<year>\d{4})")


def _swap_date(match) -> str:
    return f"{match.group('day')}-{match.group('month')}-{match.group('year')}"


def _rename_dir(root: Path, apply: bool) -> None:
    """Recursively rename files with MM-DD-YYYY dates to DD-MM-YYYY."""
    changed = 0
    skipped = 0

    for folder, _, fnames in os.walk(root):
        folder = Path(folder)

        for fn in fnames:
            newname = DATE_PAT.sub(_swap_date, fn)

            if newname == fn:
                skipped += 1
                continue

            old_path = folder / fn
            new_path = old_path.with_name(newname)

            if new_path.exists():
                print(f"SKIP (conflict): {old_path} -> {newname}")
                skipped += 1
                continue

            tag = "RENAME" if apply else "DRY-RUN"
            print(f"{tag}: {old_path} -> {newname}")

            if apply:
                old_path.rename(new_path)

            changed += 1

    status = "renamed" if apply else "would rename"
    print(f"\nDONE. {changed} {status}, {skipped} skipped.")
    if not apply:
        print("Run with --apply to rename for real.")


def _convert_file(path: Path, apply: bool) -> None:
    """Convert dates inside file content."""
    text = path.read_text(encoding="utf-8")
    result = DATE_PAT.sub(_swap_date, text)

    if not apply:
        print(result, end="")
        return

    # atomic write
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8",
        delete=False, dir=path.parent
    ) as tmp:
        tmp.write(result)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)

    try:
        tmp_path.replace(path)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise

    dir_fd = os.open(path.parent, os.O_DIRECTORY)
    os.fsync(dir_fd)
    os.close(dir_fd)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert American-style dates to European-style.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 eu_date.py ./downloads\n"
            "  python3 eu_date.py ./downloads -a\n"
            "  python3 eu_date.py notes.txt\n"
            "  python3 eu_date.py notes.txt -a"
        )
    )
    parser.add_argument("target", type=Path, help="Directory or file to process")
    parser.add_argument("--apply", "-a", action="store_true",
                        help="Rename files / overwrite in place (default is dry-run / stdout)")
    args = parser.parse_args()

    if args.target.is_dir():
        _rename_dir(args.target, apply=args.apply)
    elif args.target.is_file():
        _convert_file(args.target, apply=args.apply)
    else:
        parser.error(f"Not a file or directory: '{args.target}'")


if __name__ == "__main__":
    main()"""
eu_date.py -- rename files with American-style dates (MM-DD-YYYY)
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
