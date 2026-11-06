"""Put the throwaway test database into a known state (used by test_api and test_startup)."""
from contextlib import contextmanager

from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import event, text

import app.models  # noqa: F401  registers the tables
from app.database import Base, engine
from app.db_check import _config


def clear_test_database():
    """Remove every table, including Alembic's version table."""
    Base.metadata.drop_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))


def reset_test_database(revision="head"):
    """An empty database recorded at `revision` (default: the latest migration).

    Built with create_all() and stamped (decision M3, option A): fast, and safe
    because tests/test_migrations.py proves that create_all() and the
    migrations produce an identical schema.
    """
    clear_test_database()
    Base.metadata.create_all(bind=engine)
    if revision is not None:
        with engine.begin() as conn:
            MigrationContext.configure(conn).stamp(ScriptDirectory.from_config(_config()), revision)


@contextmanager
def foreign_keys_enforced():
    """Turn on SQLite foreign-key enforcement for every connection the app opens.

    Since P0-5 step 6 the app does this itself (app.database.enforce_foreign_keys),
    so this is now belt and braces: it keeps tests/test_order_delete_fk.py
    explicit about needing enforcement even if the app's setting were removed.
    tests/test_foreign_keys.py checks the app's own setting without it.
    """
    def _on(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys = ON")

    engine.dispose()                      # drop pooled connections opened without it
    event.listen(engine, "connect", _on)
    try:
        yield
    finally:
        event.remove(engine, "connect", _on)
        engine.dispose()                  # don't leak enforced connections to other tests
