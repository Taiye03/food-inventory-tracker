"""Put the throwaway test database into a known state (used by test_api and test_startup)."""
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text

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
