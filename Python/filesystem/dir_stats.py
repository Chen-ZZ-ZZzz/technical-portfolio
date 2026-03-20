"""
dir_stats.py -- Compute file statistics for a directory tree efficiently

Usage:
    python3 dir_stats.py <directory>

Output:
    Number of files, total size, and largest file.

Uses os.walk() with raw strings to minimize object creation
for large directory trees.

"""

import argparse
import os
from pathlib import Path


def dir_stats(root: Path) -> tuple[int, float, float, str | None]:
    """Return (count, total_mb, largest_mb, largest_path) for a directory tree.

    Uses os.walk() and os.stat() with raw strings to avoid
    creating Path objects for every entry.
    """
    root = str(root)
    count = 0
    total = 0
    largest_size = 0
    largest_path = None

    for dirpath, _, filenames in os.walk(root):
        for fname in filenames:
            fullpath = os.path.join(dirpath, fname)

            try:
                size = os.stat(fullpath).st_size
            except OSError:
                continue

            count += 1
            total += size

            if size > largest_size:
                largest_size = size
                largest_path = fullpath

    total_mb = total / (1024 * 1024)
    largest_mb = largest_size / (1024 * 1024)

    return count, total_mb, largest_mb, largest_path


def main():
    parser = argparse.ArgumentParser(description="file statistics for a directory")
    parser.add_argument("directory", help="root directory to scan")
    args = parser.parse_args()

    count, total_mb, largest_mb, largest_path = dir_stats(Path(args.directory))

    print(
        f"There are {count} files in {args.directory}.\n"
        f"Total size: {total_mb:.2f} MB\n"
        f"Largest file: {largest_path} ({largest_mb:.2f} MB)"
    )


if __name__ == "__main__":
    main()
