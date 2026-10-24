"""
Database connection.

Locally this uses a SQLite file (foodheroes.db), so there's nothing to install.
To switch to MySQL later, set one environment variable and change no code:

    export DATABASE_URL="mysql+pymysql://user:password@localhost:3306/foodheroes"

(and `pip install pymysql`).
"""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./foodheroes.db")

# SQLite needs this flag when FastAPI uses it from multiple threads.
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """Give each request its own database session, and close it afterwards."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
