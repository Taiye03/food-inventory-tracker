"""
Startup and the ledger import refuse a database that isn't at the latest
migration (P0-5 step 4), and never create or migrate anything themselves.

Unit tests use disposable databases in tmp_path. The app tests use the
throwaway test database that conftest.py points DATABASE_URL at.
"""
import os
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from app.database import Base, engine
from app.db_check import DatabaseNotReady, check_database_revision, latest_revision
from app.main import app
from scripts.db_common import file_sha256
from tests.db_helpers import clear_test_database, reset_test_database

BACKEND = Path(__file__).resolve().parents[1]
HEAD = latest_revision()


# ---------- helpers ----------
def migrate(db, target):
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    cfg.attributes["skip_logging_config"] = True
    command.upgrade(cfg, target)


def engine_for(db):
    return create_engine(f"sqlite:///{db}", poolclass=NullPool)


def unversioned_db(db):
    e = engine_for(db)
    Base.metadata.create_all(e)
    with e.begin() as conn:
        conn.execute(text("INSERT INTO users (username, fullname, role, email, password_hash) "
                          "VALUES ('mum', 'Owner', 'Admin', 'o@example.com', 'x')"))
    e.dispose()
    return db


def fingerprint(path):
    st = os.stat(path)
    return file_sha256(path), st.st_size, st.st_mtime_ns


def refused(db):
    e = engine_for(db)
    try:
        with pytest.raises(DatabaseNotReady) as info:
            check_database_revision(e)
    finally:
        e.dispose()
    return info.value


# ---------- the check itself ----------
def test_latest_revision_is_the_newest_migration():
    assert HEAD == "0002"


def test_ready_at_latest_revision(tmp_path):
    db = tmp_path / "fresh.db"
    migrate(db, "head")
    e = engine_for(db)
    assert check_database_revision(e) == HEAD
    e.dispose()


def test_missing_file_is_refused_and_not_created(tmp_path):
    db = tmp_path / "nowhere" / "foodheroes.db"
    err = refused(db)
    assert err.state == "missing"
    assert "alembic upgrade head" in str(err) and str(db) in str(err)
    assert not db.exists() and not db.parent.exists()


@pytest.mark.parametrize("how", ["empty file", "downgraded to base"])
def test_database_without_tables_is_refused(tmp_path, how):
    db = tmp_path / "empty.db"
    if how == "empty file":
        sqlite3.connect(db).close()
    else:
        migrate(db, "head")
        cfg = Config(str(BACKEND / "alembic.ini"))
        cfg.set_main_option("script_location", str(BACKEND / "migrations"))
        cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
        cfg.attributes["skip_logging_config"] = True
        command.downgrade(cfg, "base")
    err = refused(db)
    assert err.state == "missing" and "alembic upgrade head" in str(err)


def test_unversioned_database_is_refused_with_stamp_instructions(tmp_path):
    db = unversioned_db(tmp_path / "existing.db")
    before = fingerprint(db)
    err = refused(db)
    assert err.state == "unversioned"
    msg = str(err)
    assert "Do NOT run 'alembic upgrade head' on it directly" in msg
    steps = msg[msg.index("  1. "):]                 # the numbered steps, after the warning
    assert steps.index("db_preflight") < steps.index("backup_db") < steps.index("alembic stamp 0001") \
        < steps.index("alembic upgrade head") < steps.rindex("db_preflight")
    # every step names the same file, so none can fall back to a different ./foodheroes.db
    assert f"db_preflight {db}" in steps and f"backup --source {db}" in steps
    assert steps.count(f"DATABASE_URL=sqlite:///{db}") == 2
    assert fingerprint(db) == before


def test_outdated_database_is_refused_with_upgrade_instructions(tmp_path):
    db = tmp_path / "old.db"
    migrate(db, "0001")
    before = fingerprint(db)
    err = refused(db)
    assert err.state == "outdated"
    assert "at migration 0001; this code needs 0002" in str(err)
    assert str(err).index("backup_db") < str(err).index("alembic upgrade head")
    assert f"backup --source {db}" in str(err) and f"db_preflight {db}" in str(err)
    assert fingerprint(db) == before                      # nothing migrated automatically


def test_unknown_revision_is_refused(tmp_path):
    db = tmp_path / "future.db"
    migrate(db, "head")
    con = sqlite3.connect(db)
    con.execute("UPDATE alembic_version SET version_num = 'ffff'")
    con.commit()
    con.close()
    err = refused(db)
    assert err.state == "unknown" and "update the code, don't change the database" in str(err)


def test_messages_use_the_full_path(tmp_path, monkeypatch):
    """A relative DATABASE_URL must not be repeated back relative: alembic might
    be run from another folder and hit a different file."""
    monkeypatch.chdir(tmp_path)
    e = create_engine("sqlite:///./relative.db", poolclass=NullPool)
    with pytest.raises(DatabaseNotReady) as info:
        check_database_revision(e)
    assert f"DATABASE_URL=sqlite:///{tmp_path / 'relative.db'}" in str(info.value)


# ---------- the app's startup ----------
def admin_count():
    with engine.connect() as conn:
        return conn.execute(text("SELECT COUNT(*) FROM users")).scalar()


def test_app_starts_and_seeds_admin_at_latest_revision():
    reset_test_database()
    with TestClient(app) as c:
        assert c.get("/").json() == {"status": "ok"}
        r = c.post("/auth/login", data={"username": "admin", "password": "changeme123"})
        assert r.status_code == 200
    clear_test_database()


def test_app_refuses_missing_database_without_creating_it():
    path = Path(engine.url.database).resolve()
    clear_test_database()
    engine.dispose()
    path.unlink()
    with pytest.raises(DatabaseNotReady, match="No database at"):
        with TestClient(app):
            pass
    assert not path.exists()                              # nothing created


def test_app_refuses_unversioned_database_and_seeds_nothing():
    reset_test_database(revision=None)                    # tables, but no alembic_version
    with pytest.raises(DatabaseNotReady, match="isn't under migration control"):
        with TestClient(app):
            pass
    assert admin_count() == 0                             # seed_admin never ran
    with engine.connect() as conn:
        assert "alembic_version" not in {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master"))}
    clear_test_database()


def test_app_refuses_outdated_database_and_does_not_migrate_it():
    reset_test_database(revision="0001")
    with pytest.raises(DatabaseNotReady, match="this code needs 0002"):
        with TestClient(app):
            pass
    assert admin_count() == 0
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0001"
    clear_test_database()


# ---------- the ledger import ----------
def test_import_refuses_unversioned_database_and_imports_nothing(tmp_path):
    from scripts.import_ledger import main as import_main
    reset_test_database(revision=None)
    ledger = tmp_path / "ledger.csv"
    ledger.write_text("Date,Customer,Cartons (ctn),Amount (₦),Status,Notes\n2026-02-06,4u,38,2280000.0,Paid,\n",
                      encoding="utf-8")
    with pytest.raises(DatabaseNotReady):
        import_main(str(ledger))
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM orders")).scalar() == 0
    clear_test_database()
