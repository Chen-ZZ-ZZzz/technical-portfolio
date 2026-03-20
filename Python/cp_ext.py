"""
my_cp.py — copy files by extension into separate destination folders

Usage:
    python3 my_cp.py <source_dir> <dest_dir>

Example:
    python3 my_cp.py ./downloads ./sorted
    → copies .pdf files to ./sorted/new_pdf_f/
    → copies .jpg files to ./sorted/new_jpg_f/
"""

import argparse
import shutil
from pathlib import Path

DESTINATIONS = {
    '.pdf': 'new_pdf_f',
    '.jpg': 'new_jpg_f',
}


def copy_by_extension(src_root: Path, base_dst: Path) -> None:
    dirs = {ext: (base_dst / name) for ext, name in DESTINATIONS.items()}
    for dst in dirs.values():
        dst.mkdir(parents=True, exist_ok=True)

    count = 0
    for file in src_root.rglob('*'):
        if file.is_file():
            dst = dirs.get(file.suffix.lower())
            if dst:
                shutil.copy(file, dst)
                print(f'Copied: {file.name} → {dst}')
                count += 1

    print(f'Done — {count} file(s) copied.')


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Copy files by extension into separate folders.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'Example:\n'
            '  python3 my_cp.py ./downloads ./sorted'
        )
    )
    parser.add_argument('source', type=Path, help='Source directory to scan')
    parser.add_argument('destination', type=Path, help='Base destination directory')
    args = parser.parse_args()

    if not args.source.is_dir():
        parser.error(f"Source directory not found: '{args.source}'")

    copy_by_extension(args.source, args.destination)


if __name__ == '__main__':
    main()
