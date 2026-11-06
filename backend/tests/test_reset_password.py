"""
scripts/reset_password.py: changes only one user's password hash, refuses
anything that isn't a migrated live database, and never shows secrets.
Every database here is a disposable file in tmp_path.
"""
import os
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.auth import hash_password, verify_password
from app.database import Base, get_db
from app.main import app
from scripts.backup_db import backup
from scripts.db_common import file_sha256
from scripts.reset_password import main
from tests.db_helpers import clear_test_database, reset_test_database

BACKEND = Path(__file__).resolve().parents[1]
OLD = "old password for tests"
NEW = "correct horse battery staple"
TABLES = ("users", "products", "orders", "order_allocations")


# ---------- disposable databases ----------
def migrate(db, target="head"):
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    cfg.attributes["skip_logging_config"] = True
    command.upgrade(cfg, target)


def add_business_data(db):
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys = ON")
    con.executemany("INSERT INTO users (id, username, fullname, role, email, password_hash) VALUES (?,?,?,?,?,?)", [
        (1, "admin", "Administrator", "Admin", "admin@example.com", hash_password(OLD)),
        (2, "ada", "Ada Staff", "User", "ada@example.com", hash_password("staff password 1"))])
    con.execute("INSERT INTO products VALUES (1, 'BATCH-20261023-001', 'Eggs (crate)', 4500.0, 20.0, '2026-10-23', '2026-11-25')")
    con.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?)", [
        (1, "2026-02-06", "4u", "Eggs (carton)", 38, 2280000.0, 2280000.0, None),
        (2, "2026-02-12", "ACSK", "Eggs (carton)", None, 3000000.0, 1000000.0, "Name unclear"),
        (3, "2026-11-05", "Pharmcare", "Eggs (crate)", 2.5, 11250.0, 0.0, None)])
    con.execute("INSERT INTO order_allocations VALUES (1, 3, 1, 'BATCH-20261023-001', 2.5)")
    con.commit()
    con.close()


@pytest.fixture()
def live(tmp_path):
    db = tmp_path / "FoodHeroes-data" / "foodheroes.db"
    db.parent.mkdir()
    migrate(db)
    add_business_data(db)
    return db


def everything(db):
    """Every row of every table, with storage types."""
    con = sqlite3.connect(db)
    out = {}
    for t in TABLES + ("alembic_version",):
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({t})")]
        out[t] = con.execute(f"SELECT {', '.join(cols)}, {', '.join(f'typeof({c})' for c in cols)} FROM {t}").fetchall()
    con.close()
    return out


def hash_of(db, username):
    con = sqlite3.connect(db)
    h = con.execute("SELECT password_hash FROM users WHERE username = ?", (username,)).fetchone()[0]
    con.close()
    return h


def fingerprint(path):
    st = os.stat(path)
    return file_sha256(path), st.st_size, st.st_mtime_ns


def answers(*values):
    """Stand-in for getpass: returns the given answers in order, and records the prompts."""
    it = iter(values)
    asked = []

    def prompt(text):
        asked.append(text)
        return next(it)
    prompt.asked = asked
    return prompt


def run(db, username="admin", prompt=None):
    return main(["--database", str(db), "--username", username], prompt=prompt or answers(NEW, NEW))


# ---------- the reset itself ----------
def test_reset_changes_only_that_users_password_hash(live):
    before = everything(live)
    staff_hash = hash_of(live, "ada")
    assert run(live) == 0

    after = everything(live)
    for t in ("products", "orders", "order_allocations", "alembic_version"):
        assert after[t] == before[t], t                                   # business records untouched
    strip = lambda rows: [r[:5] + r[6:11] + r[12:] for r in rows]         # drop password_hash and its typeof  # noqa: E731
    assert strip(after["users"]) == strip(before["users"])                # id, username, role, email unchanged
    assert hash_of(live, "ada") == staff_hash                             # the other user untouched
    new_hash = hash_of(live, "admin")
    assert verify_password(NEW, new_hash) and not verify_password(OLD, new_hash)


def test_new_password_logs_in_through_the_api_and_old_one_does_not(live):
    assert run(live) == 0
    engine = create_engine(f"sqlite:///{live}", poolclass=NullPool)
    Session = sessionmaker(bind=engine)

    def db_override():
        s = Session()
        try:
            yield s
        finally:
            s.close()
    reset_test_database()                       # lets the app's own startup check pass
    app.dependency_overrides[get_db] = db_override
    try:
        with TestClient(app) as c:
            assert c.post("/auth/login", data={"username": "admin", "password": OLD}).status_code == 401
            r = c.post("/auth/login", data={"username": "admin", "password": NEW})
            assert r.status_code == 200
            token = r.json()["access_token"]
            assert len(c.get("/orders", headers={"Authorization": f"Bearer {token}"}).json()) == 3
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()
        clear_test_database()


def test_never_shows_passwords_or_hashes(live, capsys):
    prompt = answers(NEW, NEW)
    assert run(live, prompt=prompt) == 0
    out = capsys.readouterr()
    shown = out.out + out.err + " ".join(prompt.asked)
    assert NEW not in shown and OLD not in shown and "$2b$" not in shown
    assert "Password updated for 'admin'" in out.out


def test_prompts_with_getpass_by_default():
    import getpass
    import inspect
    assert inspect.signature(main).parameters["prompt"].default is getpass.getpass


# ---------- refusals: nothing changes ----------
@pytest.mark.parametrize("first, second, reason", [
    (NEW, NEW + "x", "don't match"),
    ("eleven char", "eleven char", "at least 12"),
    ("admin", "admin", "at least 12"),
    ("Admin" * 3, "Admin" * 3, None),          # 15 chars, allowed (not equal to the username)
    (" leading space pw", " leading space pw", "start or end with a space"),
])
def test_password_rules(live, capsys, first, second, reason):
    before = fingerprint(live)
    code = run(live, prompt=answers(first, second))
    if reason is None:
        assert code == 0
        return
    assert code == 1 and reason in capsys.readouterr().err
    assert fingerprint(live) == before


def test_refuses_password_equal_to_username(tmp_path, capsys):
    db = tmp_path / "x.db"
    migrate(db)
    con = sqlite3.connect(db)
    con.execute("INSERT INTO users VALUES (1, 'administrator1', 'A', 'Admin', 'a@example.com', 'x')")
    con.commit()
    con.close()
    before = fingerprint(db)
    assert run(db, "administrator1", answers("Administrator1", "Administrator1")) == 1
    assert "same as the username" in capsys.readouterr().err
    assert fingerprint(db) == before


def test_refuses_unknown_user_without_asking_for_a_password(live, capsys):
    before = fingerprint(live)
    prompt = answers(NEW, NEW)
    assert run(live, "Admin", prompt) == 1                                # case-sensitive: no 'Admin'
    assert "no user called 'Admin'" in capsys.readouterr().err
    assert prompt.asked == [] and fingerprint(live) == before


def test_refuses_unmigrated_database(tmp_path, capsys):
    """Like the original in Downloads: tables, data, no migration revision."""
    db = tmp_path / "original.db"
    engine = create_engine(f"sqlite:///{db}", poolclass=NullPool)
    Base.metadata.create_all(engine)
    engine.dispose()
    add_business_data(db)
    before = fingerprint(db)
    assert run(db) == 1
    assert "isn't at the latest migration" in capsys.readouterr().err
    assert fingerprint(db) == before


def test_refuses_outdated_database(tmp_path, capsys):
    db = tmp_path / "old.db"
    migrate(db, "0001")
    add_business_data(db)
    before = fingerprint(db)
    assert run(db) == 1 and "latest migration" in capsys.readouterr().err
    assert fingerprint(db) == before


def test_refuses_a_backup_even_of_a_migrated_database(live, tmp_path, capsys):
    res = backup(live, tmp_path / "FoodHeroes-backups")
    before = fingerprint(res.backup)
    assert run(res.backup) == 1
    assert "looks like a backup" in capsys.readouterr().err
    assert fingerprint(res.backup) == before


def test_refuses_missing_path_and_never_creates_it(tmp_path, capsys):
    db = tmp_path / "nowhere" / "foodheroes.db"
    assert run(db) == 1 and "No database file" in capsys.readouterr().err
    assert not db.exists() and not db.parent.exists()


def test_refuses_non_sqlite_file(tmp_path, capsys):
    f = tmp_path / "notes.db"
    f.write_text("not a database")
    assert run(f) == 1 and "not an SQLite" in capsys.readouterr().err


def test_refuses_while_a_journal_file_exists(live, capsys):
    Path(str(live) + "-journal").write_bytes(b"x")
    before = fingerprint(live)
    assert run(live) == 1 and "may be running" in capsys.readouterr().err
    assert fingerprint(live) == before


@pytest.mark.parametrize("argv", [[], ["--database", "x.db"], ["--username", "admin"]])
def test_both_arguments_are_required(argv):
    with pytest.raises(SystemExit) as e:
        main(argv, prompt=answers())
    assert e.value.code == 2


def test_database_failure_rolls_back_and_shows_no_hash(live, capsys):
    """If the database refuses the write, nothing changes and the error doesn't leak the new hash."""
    con = sqlite3.connect(live)
    con.execute("CREATE TRIGGER refuse BEFORE UPDATE ON users BEGIN SELECT RAISE(ABORT, 'blocked'); END")
    con.commit()
    con.close()
    before = everything(live)
    assert run(live) == 1
    err = capsys.readouterr().err
    assert "nothing saved" in err and "$2b$" not in err and NEW not in err
    assert everything(live) == before
