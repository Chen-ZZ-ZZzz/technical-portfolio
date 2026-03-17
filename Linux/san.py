#!/usr/bin/env python3
"""
san.py — sanitize file and directory names for shell safety

Replaces Unicode characters with ASCII equivalents (ä→ae, ß→ss, é→e, etc.)
then replaces any remaining non-safe characters with underscores.

Safe characters kept: A-Z a-z 0-9 . - _
Dots in extensions are preserved.
Runs dry-run by default — use --apply to rename for real.

Usage:
    ./san.py <directory> [-a] [-s] [-v]

Examples:
    ./san.py ./downloads                        # dry run, recursive
    ./san.py ./downloads --apply | -a           # rename recursively for real
    ./san.py ./downloads --shallow | -s         # dry run, current dir only
    ./san.py . --shallow --apply | -s -a        # rename current dir only

Requirements:
    pip install unidecode                       # general
    sudo apt install python3-unidecode         # Debian/Ubuntu
"""

import argparse
import re
from pathlib import Path

try:
    from unidecode import unidecode
except ImportError:
    print("Error: unidecode not installed. Run: pip install unidecode")
    raise SystemExit(1)

SAFE_PAT = re.compile(r"[^A-Za-z0-9._-]")
MULTI_UNDERSCORE = re.compile(r"_{2,}")


def sanitize(name: str) -> str:
    """
    Sanitize a filename:
    1. Transliterate Unicode → ASCII  (ä→ae, ß→ss, é→e ...)
    2. Replace unsafe chars with _
    3. Collapse multiple __ → _
    4. Strip leading/trailing underscores from stem
    """
    stem, _, ext = name.rpartition(".")
    if not stem:
        stem = name
        ext = ""
    else:
        ext = "." + ext

    stem = unidecode(stem)
    stem = SAFE_PAT.sub("_", stem)
    stem = MULTI_UNDERSCORE.sub("_", stem)
    stem = stem.strip("_")

    return stem + ext


def iter_entries(root: Path, shallow: bool):
    """Yield (dirpath, name) pairs, shallow or recursive, deepest first."""
    if shallow:
        entries = list(root.iterdir())
        # dirs last so files inside them are untouched if we only go one level
        for entry in sorted(entries, key=lambda p: p.is_dir()):
            yield root, entry.name
    else:
        for dirpath, dirs, files in root.walk(top_down=False):
            for name in files + dirs:
                yield dirpath, name


def process(root: Path, apply: bool, shallow: bool, verbose: bool) -> None:
    changed = 0
    skipped = 0

    for dirpath, name in iter_entries(root, shallow):
        new_name = sanitize(name)
        if new_name == name:
            skipped += 1
            continue

        old_path = dirpath / name
        new_path = dirpath / new_name

        if new_path.exists():
            print(f"SKIP (conflict): {old_path} → {new_name}")
            skipped += 1
            continue

        if verbose or not apply:
            print(f"{'RENAME' if apply else 'DRY-RUN'}: {old_path} → {new_name}")

        if apply:
            old_path.rename(new_path)

        changed += 1

    status = "renamed" if apply else "would rename"
    print(f"\nDone — {changed} {status}, {skipped} skipped.")
    if not apply:
        print("Run with --apply to rename for real.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sanitize file and directory names for shell safety.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ./san.py ./downloads\n"
            "  ./san.py ./downloads --apply | -a\n"
            "  ./san.py ./downloads --shallow | -s\n"
            "  ./san.py . --shallow --apply | -s -a"
        )
    )
    parser.add_argument("directory", type=Path, help="Directory to process")
    parser.add_argument("--apply", "-a", action="store_true", help="Actually rename (default is dry-run)")
    parser.add_argument("--shallow", "-s", action="store_true", help="Current directory only, no recursion")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print unchanged entries too")
    args = parser.parse_args()

    if not args.directory.is_dir():
        parser.error(f"Directory not found: '{args.directory}'")

    process(args.directory, apply=args.apply, shallow=args.shallow, verbose=args.verbose)


if __name__ == "__main__":
    main()
