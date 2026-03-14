"""
find_large.py — find files and folders exceeding a size threshold

Usage:
    python3 find_large.py <directory> [--mb <megabytes>]

Examples:
    python3 find_large.py ./downloads
    python3 find_large.py ./downloads --mb 500
"""

import argparse
from pathlib import Path

DEFAULT_MB = 100
BIG = DEFAULT_MB * 1024 * 1024


def dir_size(path: Path) -> int:
    """Return total size in bytes of all files under path."""
    total = 0
    for entry in path.rglob('*'):
        if not entry.is_file():
            continue
        try:
            total += entry.stat(follow_symlinks=False).st_size
        except OSError:
            continue
    return total


def find_large(root: Path, threshold: int = BIG) -> None:
    for f in root.iterdir():
        try:
            if f.is_file():
                size = f.stat(follow_symlinks=False).st_size
            elif f.is_dir():
                size = dir_size(f)
            else:
                continue
        except OSError:
            continue
        if size > threshold:
            print(f'{size / 1024 / 1024:.1f} MB  {f}')
    print('Done')


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Find files and folders exceeding a size threshold.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'Examples:\n'
            '  python3 find_large.py ./downloads\n'
            '  python3 find_large.py ./downloads --mb 500'
        )
    )
    parser.add_argument('directory', type=Path, help='Directory to scan')
    parser.add_argument('--mb', type=int, default=DEFAULT_MB,
                        help=f'Size threshold in MB (default: {DEFAULT_MB})')
    args = parser.parse_args()

    if not args.directory.is_dir():
        parser.error(f"Directory not found: '{args.directory}'")

    find_large(args.directory, threshold=args.mb * 1024 * 1024)


if __name__ == '__main__':
    main()
