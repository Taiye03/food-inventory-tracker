"""
Database connection.

Locally this uses a SQLite file (foodheroes.db), so there's nothing to install.
To switch to MySQL later, set one environment variable and change no code:

    export DATABASE_URL="mysql+pymysql://user:password@localhost:3306/foodheroes"

(and `pip install pymysql`).
"""
import os

from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./foodheroes.db")

# SQLite needs this flag when FastAPI uses it from multiple threads.
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
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
