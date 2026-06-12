"""common.py -- shared utilities for health-checker tools."""

import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# -- ANSI colors (disabled when stdout is piped) --

_USE_COLOR = sys.stdout.isatty() # color only when writing to a terminal


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


# -- SQLite database utilities --

DB_PATH = REPORTS_DIR / "health.db"

PORT_PING = 0  # sentinel: endpoint has no port (ping / ICMP)

_CREATE_SCHEMA = """
    CREATE TABLE IF NOT EXISTS hosts (
        id   INTEGER PRIMARY KEY,
        host TEXT NOT NULL UNIQUE
    ) STRICT;

    CREATE TABLE IF NOT EXISTS endpoints (
        id      INTEGER PRIMARY KEY,
        host_id INTEGER NOT NULL REFERENCES hosts(id),
        type    TEXT NOT NULL,
        port    INTEGER NOT NULL,
        UNIQUE (host_id, type, port)
    ) STRICT;

    CREATE TABLE IF NOT EXISTS results (
        id          INTEGER PRIMARY KEY,
        endpoint_id INTEGER NOT NULL REFERENCES endpoints(id),
        timestamp   TEXT NOT NULL,
        status      TEXT NOT NULL,
        latency_ms  REAL
    ) STRICT;

    CREATE TABLE IF NOT EXISTS log_checks (
        id        INTEGER PRIMARY KEY,
        timestamp TEXT NOT NULL,
        logfile   TEXT NOT NULL,
        status    TEXT NOT NULL
    ) STRICT;

    CREATE TABLE IF NOT EXISTS log_lines (
        id       INTEGER PRIMARY KEY,
        check_id INTEGER NOT NULL REFERENCES log_checks(id),
        line     TEXT NOT NULL
    ) STRICT;
"""


def _open_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create tables if they don't exist. executescript issues an implicit COMMIT first."""
    conn.executescript(_CREATE_SCHEMA)


def _get_or_create_host(conn: sqlite3.Connection, host: str) -> int:
    row = conn.execute("SELECT id FROM hosts WHERE host = ?", [host]).fetchone()
    if row:
        return row["id"]
    return conn.execute("INSERT INTO hosts (host) VALUES (?)", [host]).lastrowid


def _get_or_create_endpoint(
    conn: sqlite3.Connection, host_id: int, type_: str, port: int
) -> int:
    row = conn.execute(
        "SELECT id FROM endpoints WHERE host_id = ? AND type = ? AND port = ?",
        [host_id, type_, port],
    ).fetchone()
    if row:
        return row["id"]
    return conn.execute(
        "INSERT INTO endpoints (host_id, type, port) VALUES (?, ?, ?)",
        [host_id, type_, port],
    ).lastrowid
