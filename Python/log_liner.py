"""
Implement a simplified grep:
input:
root directory
regex pattern

output:
(path, line_number, line_text)
"""

import re
from typing import Iterator

def igrep(base: Path, pat: str) -> Iterator[tuple[Path, int, str]]:
    regex = re.compile(re.escape(pat)) # re.escape() is a good step

    for path in base.rglob("*"): # better streaming than produce a whole list
        if path.suffix not in (".txt", ".log"):
            continue

        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                for lineno, line in enumerate(f, start=1):
                    if regex.search(line):
                        yield path, lineno, line.rstrip("\n")
        except (PermissionError, OSError): # error should be clear
            continue
