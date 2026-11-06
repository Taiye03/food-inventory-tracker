"""
Alembic environment for FoodHeroes.

Which database: DATABASE_URL (required), or `sqlalchemy.url` when the tests set
it on the Config object. A caller may also pass an open connection in
config.attributes["connection"] (the tests use this).

SQLite needs extra care, because by default it can't roll back a failed migration:
  1. Alembic treats SQLite as non-transactional, and Python's sqlite3 driver
     commits DDL (CREATE / ALTER / DROP) immediately. So each migration runs
     inside an explicit BEGIN ... COMMIT on the raw connection: if anything
     fails, the whole migration rolls back.
  2. Foreign-key enforcement is switched OFF while migrating, because SQLite's
     table rebuilds (batch mode) briefly break references. It's always set
     back to what it was before, even if the migration fails.
  3. After the migrations and before COMMIT, PRAGMA foreign_key_check must come
     back empty, otherwise everything is rolled back. This catches foreign keys
     broken by table renames (see docs/DATABASE_RECOVERY.md).

Even so, a verified backup is the recovery plan; this only makes the common
failure case safe.
"""
import os
from contextlib import contextmanager
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, make_url
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401  registers every table on Base.metadata
from app.config import ConfigurationError
from app.config import database_url as app_database_url
from app.database import Base

config = context.config
if config.config_file_name is not None and not config.attributes.get("skip_logging_config"):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


class MigrationError(RuntimeError):
    pass


def database_url() -> str:
    """sqlalchemy.url when the tests set it on the Config; otherwise DATABASE_URL,
    validated exactly as the app validates it (required, full path, no default)."""
    url = config.get_main_option("sqlalchemy.url")
    if url:
        return url
    if not os.getenv("DATABASE_URL"):
        raise MigrationError(
            "Set DATABASE_URL to choose the database, e.g.\n"
            "    DATABASE_URL=sqlite:////tmp/fh-fresh.db alembic upgrade head\n"
            "Follow docs/DATABASE_RECOVERY.md before migrating a database with real data."
        )
    try:
        return app_database_url()
    except ConfigurationError as e:
        raise MigrationError(str(e)) from None


@contextmanager
def sqlite_safety(connection):
    """Explicit transaction + FKs off for the duration, restored afterwards."""
    if connection.in_transaction():
        raise MigrationError("Pass a connection that isn't already in a transaction")
    raw = connection.connection.driver_connection  # doesn't start a SQLAlchemy transaction
    if raw.in_transaction:
        raise MigrationError("The SQLite connection is already in a transaction")
    previous_fk = raw.execute("PRAGMA foreign_keys").fetchone()[0]
    previous_isolation = raw.isolation_level
    raw.execute("PRAGMA foreign_keys = OFF")   # only takes effect outside a transaction
    raw.isolation_level = None                 # we issue BEGIN ourselves
    try:
        yield
    finally:
        if raw.in_transaction:                 # belt and braces: never leave one open
            raw.rollback()
        raw.isolation_level = previous_isolation
        raw.execute(f"PRAGMA foreign_keys = {'ON' if previous_fk else 'OFF'}")


def run(connection):
    sqlite = connection.dialect.name == "sqlite"
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        render_as_batch=sqlite,
        transactional_ddl=True,
    )
    if not sqlite:
        with context.begin_transaction():
            context.run_migrations()
        return
    with sqlite_safety(connection):
        with context.begin_transaction():
            connection.exec_driver_sql("BEGIN")
            context.run_migrations()
            broken = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            if broken:
                raise MigrationError(
                    f"Migration left {len(broken)} broken foreign key reference(s), e.g. {broken[:3]}. "
                    "Everything has been rolled back."
                )


def run_migrations_online():
    supplied = config.attributes.get("connection")
    if supplied is not None:
        run(supplied)
        return
    url = database_url()
    print(f"Target database: {make_url(url).render_as_string(hide_password=True)}")
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            run(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    raise MigrationError("Offline (SQL script) mode isn't supported; run against a database.")
run_migrations_online()
