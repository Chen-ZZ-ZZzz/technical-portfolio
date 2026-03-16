"""
sanitize_names.py — sanitize file and directory names for shell safety

Replaces Unicode characters with ASCII equivalents (ä→ae, ß→ss, é→e, etc.)
then replaces any remaining non-safe characters with underscores.

Safe characters kept: A-Z a-z 0-9 . - _
Dots in extensions are preserved.
Runs dry-run by default — use --apply to rename for real.

Usage:
    python3 sanitize_names.py <directory> [--apply] [--verbose]

Examples:
    python3 sanitize_names.py ./downloads           # dry run, preview changes
    python3 sanitize_names.py ./downloads --apply   # rename for real

Requirements:
    pip install unidecode
"""

import argparse
import re
from pathlib import Path

try:
    from unidecode import unidecode
except ImportError:
    print("Error: unidecode not installed. Run: pip install unidecode")
    raise SystemExit(1)

# Characters safe to keep as-is (besides A-Za-z0-9)
SAFE_PAT = re.compile(r"[^A-Za-z0-9._-]")
MULTI_UNDERSCORE = re.compile(r"_{2,}")


def sanitize(name: str) -> str:
    """
    Sanitize a filename:
    1. Transliterate Unicode → ASCII  (ä→ae, ß→ss, é→e …)
    2. Replace unsafe chars with _
    3. Collapse multiple __ → _
    4. Strip leading/trailing underscores from stem
    """
    stem, _, ext = name.rpartition(".")
    if not stem:
        # no extension (e.g. dotfiles like .bashrc) or pure extension
        stem = name
        ext = ""
    else:
        ext = "." + ext

    stem = unidecode(stem)
    stem = SAFE_PAT.sub("_", stem)
    stem = MULTI_UNDERSCORE.sub("_", stem)
    stem = stem.strip("_")

    return stem + ext


def process(root: Path, apply: bool, verbose: bool) -> None:
    changed = 0
    skipped = 0

    # top_down=False: rename deepest entries first to avoid broken paths
    for dirpath, dirs, files in root.walk(top_down=False):
        entries = files + dirs
        for name in entries:
            new_name = sanitize(name)
            if new_name == name:
                skipped += 1
                continue

            old_path = dirpath / name
            new_path = dirpath / new_name

            if new_path.exists():
                print(f"SKIP (conflict): {old_path} → {new_path}")
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
            "  python3 sanitize_names.py ./downloads\n"
            "  python3 sanitize_names.py ./downloads --apply\n"
            "  python3 sanitize_names.py ./downloads --apply --verbose"
        )
    )
    parser.add_argument("directory", type=Path, help="Directory to process recursively")
    parser.add_argument("--apply", action="store_true", help="Actually rename (default is dry-run)")
    parser.add_argument("--verbose", action="store_true", help="Print all renames including unchanged")
    args = parser.parse_args()

    if not args.directory.is_dir():
        parser.error(f"Directory not found: '{args.directory}'")

    process(args.directory, apply=args.apply, verbose=args.verbose)


if __name__ == "__main__":
    main()
