"""
Database connection.

Locally this uses a SQLite file (foodheroes.db), so there's nothing to install.
To switch to MySQL later, set one environment variable and change no code:

    export DATABASE_URL="mysql+pymysql://user:password@localhost:3306/foodheroes"

(and `pip install pymysql`).
"""
import os

from sqlalchemy import MetaData, create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./foodheroes.db")

# SQLite needs this flag when FastAPI uses it from multiple threads.
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}


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


engine = create_engine(DATABASE_URL, connect_args=connect_args)
enforce_foreign_keys(engine)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

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
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
