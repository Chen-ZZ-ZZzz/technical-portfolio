"""
log_reader.py -- Find lines that match patterns from log files in a directory

Usage:
    python3 log_reader.py <directory> <pattern>

"""

import argparse
import re
from pathlib import Path
from typing import Iterator


def igrep(base: Path, pat: str) -> Iterator[tuple[Path, int, str]]:
    regex = re.compile(pat)

    for path in base.rglob("*"):
        if path.suffix not in (".txt", ".log"):
            continue

        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                for lineno, line in enumerate(f, start=1):
                    if regex.search(line):
                        yield path, lineno, line.rstrip("\n")
        except (PermissionError, OSError):
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
