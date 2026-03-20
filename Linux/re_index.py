#!/usr/bin/env python3
"""
re_index.py -- renumber or gap-insert prefixed files in a directory

Dry-run by default -- use --apply to rename for real.

Usage:
    python3 re_index.py <directory> <prefix>
    python3 re_index.py <directory> <prefix> --insert-gap <start> <width>
    python3 re_index.py <directory> <prefix> --apply

Examples:
    python3 re_index.py ./docs spam              # dry run: show what would change
    python3 re_index.py ./docs spam -a           # rename for real
    python3 re_index.py ./docs spam --insert-gap 3 2       # dry run gap insert
    python3 re_index.py ./docs spam --insert-gap 3 2 -a    # gap insert for real
"""

import argparse
from pathlib import Path


def renumber(prefix: str, root: Path, apply: bool) -> None:
    """Close gaps in numbered files e.g. spam001, spam003 -> spam001, spam002."""
    prefix_len = len(prefix)
    index = 1
    changed = 0

    for f in sorted(root.glob(f"{prefix}*")):
        if not f.is_file():
            continue
        suffix = f.stem[prefix_len:]
        if not suffix.isdigit():
            continue
        if int(suffix) != index:
            new_name = f"{prefix}{str(index).zfill(len(suffix))}{f.suffix}"
            tag = "RENAME" if apply else "DRY-RUN"
            print(f"{tag}: {f.name} -> {new_name}")
            if apply:
                f.rename(f.parent / new_name)
            changed += 1
        index += 1

    _print_summary(changed, apply)


def insert_gap(prefix: str, root: Path, start: int, width: int, apply: bool) -> None:
    """Shift all numbered files >= start upward by width."""
    prefix_len = len(prefix)
    numbered = []
    changed = 0

    for f in root.iterdir():
        if not f.is_file():
            continue
        if not f.stem.startswith(prefix):
            continue
        suffix = f.stem[prefix_len:]
        if not suffix.isdigit():
            continue
        numbered.append((int(suffix), f))

    for num, f in sorted(numbered, key=lambda x: x[0], reverse=True):
        if num < start:
            continue
        new_name = f"{prefix}{str(num + width).zfill(len(f.stem) - prefix_len)}{f.suffix}"
        tag = "RENAME" if apply else "DRY-RUN"
        print(f"{tag}: {f.name} -> {new_name}")
        if apply:
            f.rename(f.parent / new_name)
        changed += 1

    _print_summary(changed, apply)


def _print_summary(changed: int, apply: bool) -> None:
    status = "renamed" if apply else "would rename"
    print(f"\nDONE. {changed} {status}.")
    if not apply:
        print("Run with --apply to rename for real.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Renumber or gap-insert prefixed files. Dry-run by default.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 re_index.py ./docs spam\n"
            "  python3 re_index.py ./docs spam --apply\n"
            "  python3 re_index.py ./docs spam --insert-gap 3 2"
        )
    )
    parser.add_argument("directory", type=Path, help="Target directory")
    parser.add_argument("prefix", help="Filename prefix e.g. spam")
    parser.add_argument("-a", "--apply", action="store_true", help="Actually rename (default is dry-run)")
    parser.add_argument("-i", "--insert-gap", nargs=2, type=int, metavar=("START", "WIDTH"),
                        help="Insert a gap of WIDTH at START instead of closing gaps")
    args = parser.parse_args()

    if not args.directory.is_dir():
        parser.error(f"Directory not found: '{args.directory}'")

    if args.insert_gap:
        insert_gap(args.prefix, args.directory, *args.insert_gap, apply=args.apply)
    else:
        renumber(args.prefix, args.directory, apply=args.apply)


if __name__ == "__main__":
    main()
