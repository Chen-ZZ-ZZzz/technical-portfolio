"""
log_reader.py -- Find lines that match patterns from log files in a directory

Usage:
    python3 log_reader.py <directory> <pattern>

"""

import argparse
import re
from pathlib import Path
from typing import Iterator


def _scan_file(f, path, regex):
    for lineno, line in enumerate(f, start=1):
        if regex.search(line):
            yield path, lineno, line.rstrip("\n")


def igrep(base: Path, pat: str) -> Iterator[tuple[Path, int, str]]:
    regex = re.compile(pat)

    for path in base.rglob("*"):
        if not path.is_file() or path.suffix not in (".txt", ".log"):
            continue

        try:
            try:
                with path.open("r", encoding="utf-8") as f:
                    yield from _scan_file(f, path, regex)

            except UnicodeDecodeError:
                with path.open("r", encoding="latin-1") as f:
                    yield from _scan_file(f, path, regex)

        except (PermissionError, OSError) as e:
            print(f"[SKIPPED] {path} ({e})")
            continue


def main():
    parser = argparse.ArgumentParser(description="find lines that match patterns")
    parser.add_argument("directory", help="Path to the directory")
    parser.add_argument("pattern", help="regex pattern")
    args = parser.parse_args()

    for path, lineno, line in igrep(Path(args.directory), args.pattern):
        print(f"{path}: {lineno}: {line}")


if __name__ == "__main__":
    main()
