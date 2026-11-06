"""
Database connection.

Which database: DATABASE_URL, which is required and validated by
app/config.py (full path for SQLite; no default, so a forgotten setting can
never quietly open some other ./foodheroes.db). PostgreSQL later needs only a
different DATABASE_URL (see docs/CONFIGURATION.md).

The engine is created the first time it's needed, not when this module is
imported. So the command-line tools that take an explicit --database
(create_admin, reset_password) and anything that only needs the models
(Base) work without DATABASE_URL, while the app and import_ledger refuse to
touch any database until DATABASE_URL is set correctly.

`engine` and `SessionLocal` can still be imported as before
(`from app.database import engine`): that creates the engine at that moment.
"""
import threading

from sqlalchemy import MetaData, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import database_url


def enforce_foreign_keys(engine) -> bool:
    """Make every new SQLite connection of `engine` enforce foreign keys (P0-5 step 6).

    SQLite ignores FOREIGN KEY constraints unless each connection turns them on,
    and the setting can't be stored in the database file. So it's switched on
    the moment each connection is opened, before any transaction starts.

    Only SQLite needs this: PostgreSQL and MySQL always enforce foreign keys,
    so nothing is registered for them (returns False).

    Alembic is unaffected: migrations/env.py creates its own engine without this
    listener, and turns enforcement off while migrating (then restores it).
    """
    if engine.dialect.name != "sqlite":
        return False

    @event.listens_for(engine, "connect")
    def _sqlite_foreign_keys_on(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    return True


_lock = threading.Lock()
_engine: Engine | None = None
_sessionmaker = None


def get_engine() -> Engine:
    """The app's engine, created on first use from the validated DATABASE_URL.
    Raises ConfigurationError if DATABASE_URL is missing or unsafe."""
    global _engine, _sessionmaker
    with _lock:
        if _engine is None:
            url = database_url()
            # SQLite needs this flag when FastAPI uses it from multiple threads.
            connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
            # hide_parameters: database error messages (and so tracebacks in the
            # server log) show "[SQL parameters hidden]" instead of the values
            # being written, such as a new user's password hash.
            engine = create_engine(url, connect_args=connect_args, hide_parameters=True)
            enforce_foreign_keys(engine)
            _sessionmaker = sessionmaker(autocommit=False, autoflush=False, bind=engine)
            _engine = engine
        return _engine


def get_sessionmaker():
    get_engine()
    return _sessionmaker


def __getattr__(name):
    """`from app.database import engine / SessionLocal` keeps working (PEP 562)."""
    if name == "engine":
        return get_engine()
    if name == "SessionLocal":
        return get_sessionmaker()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# Predictable names for indexes and constraints (P0-5, decision M4). Migrations
# refer to constraints by name, and PostgreSQL would otherwise invent its own.
# The "ix" pattern is SQLAlchemy's default, so existing index names don't change.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
Base = declarative_base(metadata=MetaData(naming_convention=NAMING_CONVENTION))


def get_db():
    """Give each request its own database session, and close it afterwards."""
    db = get_sessionmaker()()
    try:
        yield db
    finally:
        db.close()
