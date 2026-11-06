"""
Safety checks shared by the tools that WRITE to the live database
(scripts/create_admin.py and scripts/reset_password.py).

Unlike scripts/db_common.py, which only ever opens databases read-only, these
tools change accounts, so before anything is written they must be sure the
file is the live, migrated database and that nothing else is using it.

    path = live_database_path(args.database, action="Reset passwords")
    engine = open_live_engine(path)
    try:
        require_latest_migration(engine)
        ...
    finally:
        engine.dispose()

Every check raises NotLiveDatabase with a plain-words reason. Nothing is
created, written or deleted by any function here.
"""
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from app.database import enforce_foreign_keys
from app.db_check import DatabaseNotReady, check_database_revision
from scripts.db_common import is_sqlite_file

BUSY_SUFFIXES = ("-journal", "-wal")


class NotLiveDatabase(Exception):
    """This file isn't safe to change; nothing has been changed."""


def live_database_path(raw: str, action: str) -> Path:
    """Resolve `raw` and refuse anything that isn't an idle, live SQLite database.

    `action` names what the tool does, for the backup message
    (e.g. "Create accounts", "Reset passwords").
    """
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise NotLiveDatabase(f"No database file at {path}. This tool never creates one; "
                              "a new database is made with `alembic upgrade head`.")
    if not is_sqlite_file(path):
        raise NotLiveDatabase(f"{path} is not an SQLite database.")
    if Path(str(path) + ".json").exists():
        raise NotLiveDatabase(f"{path} has a backup manifest beside it: it looks like a backup. "
                              f"{action} only in the live database.")
    for suffix in BUSY_SUFFIXES:
        if Path(str(path) + suffix).exists():
            raise NotLiveDatabase(f"{path.name}{suffix} exists: the app may be running. Stop it first.")
    return path


def open_live_engine(path: Path) -> Engine:
    """An engine for `path` with the app's foreign-key enforcement, no pooled
    connections left open, and hide_parameters so that a database error never
    includes the values (such as a password hash) that were being written."""
    engine = create_engine(f"sqlite:///{path}", poolclass=NullPool, hide_parameters=True)
    enforce_foreign_keys(engine)
    return engine


def require_latest_migration(engine: Engine) -> None:
    """Every pre-migration database and every old backup fails this."""
    try:
        check_database_revision(engine)
    except DatabaseNotReady as e:
        raise NotLiveDatabase("This database isn't at the latest migration, so it isn't a live, migrated "
                              f"database (it may be an old copy, or need migrating first).\n{e}") from None
