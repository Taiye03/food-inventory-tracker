"""
Helpers shared by the backup and preflight tools.

Everything here opens databases READ-ONLY unless a function says otherwise.
Read-only means SQLite's `mode=ro` URI flag plus `PRAGMA query_only`, so a
bug in these tools can't write to the business database.
"""
import hashlib
import os
import sqlite3
from pathlib import Path
from urllib.parse import quote

SQLITE_HEADER = b"SQLite format 3\x00"


class DatabaseToolError(Exception):
    """A problem the person running the tool needs to see in plain words."""


def is_sqlite_file(path: Path) -> bool:
    """Check the 16-byte file header. Reads the file; never opens it as a database."""
    try:
        with open(path, "rb") as f:
            return f.read(16) == SQLITE_HEADER
    except OSError:
        return False


def connect_readonly(path: Path) -> sqlite3.Connection:
    """Open an existing SQLite file so that it cannot be written.

    Refuses if the file is missing (a normal connect would create it) or if a
    journal file is next to it (an interrupted write: SQLite would want to roll
    it back, which is a write). Stop the app and investigate before continuing.
    """
    path = Path(path)
    if not path.is_file():
        raise DatabaseToolError(f"No database file at {path}")
    if not is_sqlite_file(path):
        raise DatabaseToolError(f"{path} is not an SQLite database")
    for suffix in ("-journal", "-wal"):
        side = Path(str(path) + suffix)
        if side.exists() and side.stat().st_size > 0:
            raise DatabaseToolError(
                f"{side.name} exists next to the database: the app may be running or a write "
                "was interrupted. Stop the app, then investigate before backing up."
            )
    uri = f"file:{quote(str(path.resolve()))}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA query_only = ON")
    return conn


def user_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {t: conn.execute(f"SELECT COUNT(*) FROM {quote_ident(t)}").fetchone()[0] for t in user_tables(conn)}


def integrity(conn: sqlite3.Connection) -> str:
    """'ok', or SQLite's description of the damage."""
    rows = conn.execute("PRAGMA integrity_check").fetchall()
    return "; ".join(r[0] for r in rows)


def content_digest(conn: sqlite3.Connection) -> str:
    """SHA-256 over every table's rows, in a fixed order.

    Two databases with the same digest hold the same data, even if their files
    differ byte for byte (a restored backup usually does). Used to prove a
    restore brought back exactly what was backed up.
    """
    h = hashlib.sha256()
    for table in user_tables(conn):
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({quote_ident(table)})")]
        h.update(f"table {table} {cols}\n".encode())
        order = "rowid" if _has_rowid(conn, table) else ", ".join(quote_ident(c) for c in cols)
        for row in conn.execute(f"SELECT * FROM {quote_ident(table)} ORDER BY {order}"):
            h.update((repr(row) + "\n").encode())
    return h.hexdigest()


def _has_rowid(conn: sqlite3.Connection, table: str) -> bool:
    try:
        conn.execute(f"SELECT rowid FROM {quote_ident(table)} LIMIT 1")
        return True
    except sqlite3.OperationalError:
        return False


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sqlite_path_from_url(url: str) -> Path:
    """sqlite:///./foodheroes.db -> Path('./foodheroes.db'). Refuses other databases."""
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise DatabaseToolError("These tools only handle SQLite. For PostgreSQL use pg_dump.")
    return Path(url[len(prefix):])


def default_db_path() -> Path:
    return sqlite_path_from_url(os.getenv("DATABASE_URL", "sqlite:///./foodheroes.db"))
