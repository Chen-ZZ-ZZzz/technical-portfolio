"""common.py -- shared utilities for health-checker tools."""

import os
import sys
from datetime import datetime
from pathlib import Path

# -- ANSI colors (disabled when stdout is piped) --

_USE_COLOR = sys.stdout.isatty()


def _red(s: str) -> str:
    return f"\033[31m{s}\033[0m" if _USE_COLOR else s


def _green(s: str) -> str:
    return f"\033[32m{s}\033[0m" if _USE_COLOR else s


def _color_status(label: str, ok: bool) -> str:
    return _green(label) if ok else _red(label)


# -- Timestamps and report saving --

REPORTS_DIR = Path("reports")
EXT_MAP = {"table": ".txt", "json": ".json", "csv": ".csv"}


def report_timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def file_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _chown_to_real_user(*paths: Path) -> None:
    """If running under sudo, chown paths back to the real user."""
    uid = os.environ.get("SUDO_UID")
    gid = os.environ.get("SUDO_GID")
    if uid is None:
        return
    uid, gid = int(uid), int(gid) if gid else -1
    for p in paths:
        try:
            os.chown(p, uid, gid)
        except OSError:
            pass


def save_report(content: str, fmt: str, prefix: str) -> Path | None:
    """Write content to reports/{prefix}_YYYYMMDD_HHMMSS.ext and return the path."""
    try:
        REPORTS_DIR.mkdir(exist_ok=True)
    except OSError as e:
        print(f"Error: cannot create '{REPORTS_DIR}': {e}", file=sys.stderr)
        return None
    ext = EXT_MAP[fmt]
    path = REPORTS_DIR / f"{prefix}_{file_timestamp()}{ext}"
    try:
        path.write_text(content, encoding="utf-8")
    except OSError as e:
        print(f"Error: cannot write '{path}': {e}", file=sys.stderr)
        return None
    _chown_to_real_user(REPORTS_DIR, path)
    return path
