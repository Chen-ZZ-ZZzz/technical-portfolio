"""
mask.py — mask secret values in config text

Masks values for keys: password, token, api_key
Works across separators: =  :  whitespace
Quoted and unquoted values supported.
Comment lines (starting with #) are left untouched.

Usage:
    python3 mask.py <configfile>

Example:
    python3 mask.py config.txt
"""

import argparse
import re
from pathlib import Path

SECRET_PAT = re.compile(
    r"""
    (?P<key>password|token|api_key)
    (?P<sep>[= :]+)
    (?P<value>
        "[^"]*"     # quoted value
      | [^\s#]+     # unquoted: stop at space or #
    )
    """,
    re.MULTILINE | re.VERBOSE
)


def mask(txt: str, sink: list | None = None) -> str:
    """Mask secret values in config text; optionally collect (key, value) pairs in sink."""
    def rep(m: re.Match) -> str:
        val = m.group("value")
        if len(val) >= 2 and val[0] == '"' and val[-1] == '"':
            val = val[1:-1]
        if sink is not None:
            sink.append((m.group("key"), val))
        return f"{m.group('key')}{m.group('sep')}***"

    lines = [
        SECRET_PAT.sub(rep, line) if not line.strip().startswith("#") else line
        for line in txt.splitlines()
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mask secret values in config text.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  python3 mask.py config.txt"
        )
    )
    parser.add_argument("configfile", type=Path, help="Config file to mask")
    args = parser.parse_args()

    if not args.configfile.is_file():
        parser.error(f"File not found: '{args.configfile}'")

    print(mask(args.configfile.read_text(encoding="utf-8")))


if __name__ == "__main__":
    main()
