"""
scripts/create_admin.py: creates exactly one first administrator, never touches
existing accounts or business data, refuses anything that isn't a migrated
live database, and never shows secrets. Every database is a disposable file.
"""
import getpass
import inspect
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
from app.db_check import check_has_administrator
from app.main import app
from scripts.backup_db import backup
from scripts.create_admin import main
from scripts.db_common import file_sha256
from tests.db_helpers import clear_test_database, reset_test_database

BACKEND = Path(__file__).resolve().parents[1]
PASSWORD = "first owner password 2026"
DETAILS = ["--username", "owner", "--fullname", "Business Owner", "--email", "owner@example.com"]
TABLES = ("users", "products", "orders", "order_allocations", "alembic_version")


# ---------- disposable databases ----------
def migrate(db, target="head"):
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    cfg.attributes["skip_logging_config"] = True
    command.upgrade(cfg, target)


def add_business_data(db, staff=True):
    con = sqlite3.connect(db)
    if staff:
        con.execute("INSERT INTO users (id, username, fullname, role, email, password_hash) VALUES "
                    "(1, 'ada', 'Ada Staff', 'User', 'ada@example.com', ?)", (hash_password("staff password 1"),))
    con.execute("INSERT INTO products VALUES (1, 'BATCH-20261023-001', 'Eggs (crate)', 4500.0, 20.0, '2026-10-23', '2026-11-25')")
    con.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?)", [
        (1, "2026-02-06", "4u", "Eggs (carton)", 38, 2280000.0, 2280000.0, None),
        (2, "2026-11-05", "Pharmcare", "Eggs (crate)", 2.5, 11250.0, 0.0, "part of a batch")])
    con.execute("INSERT INTO order_allocations VALUES (1, 2, 1, 'BATCH-20261023-001', 2.5)")
    con.commit()
    con.close()


@pytest.fixture()
def fresh(tmp_path):
    """Migrated, with business data and a staff account, but no administrator."""
    db = tmp_path / "FoodHeroes-data" / "foodheroes.db"
    db.parent.mkdir()
    migrate(db)
    add_business_data(db)
    return db


def everything(db):
    con = sqlite3.connect(db)
    out = {}
    for t in TABLES:
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({t})")]
        out[t] = con.execute(f"SELECT {', '.join(cols)}, {', '.join(f'typeof({c})' for c in cols)} "
                             f"FROM {t} ORDER BY rowid").fetchall()
    con.close()
    return out


def users(db):
    con = sqlite3.connect(db)
    rows = con.execute("SELECT id, username, fullname, role, email, password_hash FROM users ORDER BY id").fetchall()
    con.close()
    return rows


def fingerprint(path):
    st = os.stat(path)
    return file_sha256(path), st.st_size, st.st_mtime_ns


def answers(*values):
    """Stand-in for getpass that records the prompts it was given."""
    it = iter(values)

    def prompt(text):
        prompt.asked.append(text)
        return next(it)
    prompt.asked = []
    return prompt


def run(db, *, details=DETAILS, prompt=None):
    return main(["--database", str(db), *details], prompt=prompt or answers(PASSWORD, PASSWORD))


# ---------- creating the first administrator ----------
def test_creates_exactly_one_admin_and_touches_nothing_else(fresh):
    before = everything(fresh)
    staff_before = users(fresh)
    assert run(fresh) == 0

    after = everything(fresh)
    for t in ("products", "orders", "order_allocations", "alembic_version"):
        assert after[t] == before[t], t                              # business records untouched
    rows = users(fresh)
    assert rows[:len(staff_before)] == staff_before                  # the existing account is unchanged
    new = rows[len(staff_before):]
    assert len(new) == 1
    _id, username, fullname, role, email, password_hash = new[0]
    assert (username, fullname, role, email) == ("owner", "Business Owner", "Admin", "owner@example.com")
    assert verify_password(PASSWORD, password_hash)
    engine = create_engine(f"sqlite:///{fresh}", poolclass=NullPool)
    check_has_administrator(engine)                                   # the app's startup check now passes
    engine.dispose()


def test_new_admin_can_log_in_and_use_the_api(fresh):
    assert run(fresh) == 0
    engine = create_engine(f"sqlite:///{fresh}", poolclass=NullPool)
    Session = sessionmaker(bind=engine)

    def db_override():
        s = Session()
        try:
            yield s
        finally:
            s.close()
    reset_test_database()                                             # lets the app's own startup checks pass
    app.dependency_overrides[get_db] = db_override
    try:
        with TestClient(app) as c:
            r = c.post("/auth/login", data={"username": "owner", "password": PASSWORD})
            assert r.status_code == 200
            h = {"Authorization": f"Bearer {r.json()['access_token']}"}
            assert c.get("/auth/me", headers=h).json()["role"] == "Admin"
            assert len(c.get("/orders", headers=h).json()) == 2
            assert c.post("/auth/login", data={"username": "owner", "password": "wrong password!"}).status_code == 401
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()
        clear_test_database()


def test_works_on_an_empty_new_install(tmp_path):
    db = tmp_path / "new.db"
    migrate(db)
    assert run(db) == 0
    assert [(u[1], u[3]) for u in users(db)] == [("owner", "Admin")]


def test_never_shows_the_password_or_hash(fresh, capsys):
    prompt = answers(PASSWORD, PASSWORD)
    assert run(fresh, prompt=prompt) == 0
    out = capsys.readouterr()
    shown = out.out + out.err + " ".join(prompt.asked)
    assert PASSWORD not in shown and "$2b$" not in shown
    assert "Administrator 'owner' created" in out.out


def test_prompts_with_getpass_by_default():
    assert inspect.signature(main).parameters["prompt"].default is getpass.getpass


# ---------- never touches existing accounts ----------
def test_refuses_when_an_administrator_already_exists(fresh, capsys):
    assert run(fresh) == 0
    before = fingerprint(fresh)
    prompt = answers(PASSWORD, PASSWORD)
    second = ["--username", "someone", "--fullname", "Someone Else", "--email", "s@example.com"]
    assert run(fresh, details=second, prompt=prompt) == 1
    err = capsys.readouterr().err
    assert "already has an administrator" in err and "scripts.reset_password" in err
    assert prompt.asked == [] and fingerprint(fresh) == before       # refused before asking for a password


def test_refuses_a_username_that_already_exists(fresh, capsys):
    """'ada' is a staff account: it must not be turned into, or replaced by, an admin."""
    before = fingerprint(fresh)
    prompt = answers(PASSWORD, PASSWORD)
    assert run(fresh, details=["--username", "ada", "--fullname", "Ada", "--email", "ada@example.com"], prompt=prompt) == 1
    assert "already exists" in capsys.readouterr().err
    assert prompt.asked == [] and fingerprint(fresh) == before


def test_another_admin_appearing_mid_run_is_detected_and_rolled_back(fresh, capsys):
    """If another run creates an admin while this one waits for the password,
    this one must undo itself rather than leave two first-admins."""
    calls = []

    def sneaky_prompt(text):
        calls.append(text)
        if len(calls) == 1:
            con = sqlite3.connect(fresh)
            con.execute("INSERT INTO users (username, fullname, role, email, password_hash) "
                        "VALUES ('other', 'Other', 'Admin', 'o@example.com', 'x')")
            con.commit()
            con.close()
        return PASSWORD
    assert run(fresh, prompt=sneaky_prompt) == 1
    assert "appeared while this was running" in capsys.readouterr().err
    assert [u[1] for u in users(fresh)] == ["ada", "other"]          # this run's admin was not kept


# ---------- refusals: nothing changes ----------
@pytest.mark.parametrize("first, second, reason", [
    (PASSWORD, PASSWORD + "x", "don't match"),
    ("eleven char", "eleven char", "at least 12"),
    ("a" * 73, "a" * 73, "too long"),                                # 73 bytes
    ("é" * 37, "é" * 37, "too long"),                               # 37 characters but 74 bytes
    (" leading space pw", " leading space pw", "start or end with a space"),
    ("owner", "owner", "at least 12"),                               # short, and the username
])
def test_password_rules(fresh, capsys, first, second, reason):
    before = fingerprint(fresh)
    assert run(fresh, prompt=answers(first, second)) == 1
    assert reason in capsys.readouterr().err
    assert fingerprint(fresh) == before


def test_password_equal_to_username_is_refused(fresh, capsys):
    details = ["--username", "OwnerAccount12", "--fullname", "Owner", "--email", "o@example.com"]
    assert run(fresh, details=details, prompt=answers("owneraccount12", "owneraccount12")) == 1
    assert "same as the username" in capsys.readouterr().err


@pytest.mark.parametrize("password", ["a" * 72, "é" * 36, "correct horse battery staple"])
def test_passwords_up_to_72_bytes_are_accepted(fresh, password):
    assert run(fresh, prompt=answers(password, password)) == 0
    assert verify_password(password, users(fresh)[-1][5])


@pytest.mark.parametrize("details, reason", [
    (["--username", "", "--fullname", "Owner", "--email", "o@example.com"], "can't be empty"),
    (["--username", "two words", "--fullname", "Owner", "--email", "o@example.com"], "can't contain spaces"),
    (["--username", "u" * 51, "--fullname", "Owner", "--email", "o@example.com"], "at most 50"),
    (["--username", "owner", "--fullname", " ", "--email", "o@example.com"], "can't be empty"),
    (["--username", "owner", "--fullname", "Owner", "--email", "not-an-email"], "valid email"),
])
def test_invalid_details_are_refused_before_opening_the_database(fresh, capsys, details, reason):
    before = fingerprint(fresh)
    prompt = answers(PASSWORD, PASSWORD)
    assert run(fresh, details=details, prompt=prompt) == 1
    assert reason in capsys.readouterr().err
    assert prompt.asked == [] and fingerprint(fresh) == before


def test_refuses_unmigrated_database(tmp_path, capsys):
    """Like the original in Downloads: tables and data, but no migration revision."""
    db = tmp_path / "original.db"
    engine = create_engine(f"sqlite:///{db}", poolclass=NullPool)
    Base.metadata.create_all(engine)
    engine.dispose()
    add_business_data(db, staff=False)
    before = fingerprint(db)
    assert run(db) == 1 and "isn't at the latest migration" in capsys.readouterr().err
    assert fingerprint(db) == before


def test_refuses_outdated_database(tmp_path, capsys):
    db = tmp_path / "old.db"
    migrate(db, "0001")
    before = fingerprint(db)
    assert run(db) == 1 and "latest migration" in capsys.readouterr().err
    assert fingerprint(db) == before


def test_refuses_a_backup(fresh, tmp_path, capsys):
    res = backup(fresh, tmp_path / "FoodHeroes-backups")
    before = fingerprint(res.backup)
    assert run(res.backup) == 1 and "looks like a backup" in capsys.readouterr().err
    assert fingerprint(res.backup) == before


def test_refuses_missing_path_and_never_creates_it(tmp_path, capsys):
    db = tmp_path / "nowhere" / "foodheroes.db"
    assert run(db) == 1 and "No database file" in capsys.readouterr().err
    assert not db.exists() and not db.parent.exists()


def test_refuses_non_sqlite_file(tmp_path, capsys):
    f = tmp_path / "notes.db"
    f.write_text("not a database")
    assert run(f) == 1 and "not an SQLite" in capsys.readouterr().err


def test_refuses_while_a_journal_file_exists(fresh, capsys):
    Path(str(fresh) + "-journal").write_bytes(b"x")
    before = fingerprint(fresh)
    assert run(fresh) == 1 and "may be running" in capsys.readouterr().err
    assert fingerprint(fresh) == before


@pytest.mark.parametrize("missing", ["--database", "--username", "--fullname", "--email"])
def test_all_arguments_are_required(fresh, missing):
    argv = ["--database", str(fresh), *DETAILS]
    i = argv.index(missing)
    del argv[i:i + 2]
    with pytest.raises(SystemExit) as e:
        main(argv, prompt=answers())
    assert e.value.code == 2


def test_database_failure_rolls_back_and_shows_no_hash(fresh, capsys):
    con = sqlite3.connect(fresh)
    con.execute("CREATE TRIGGER refuse BEFORE INSERT ON users BEGIN SELECT RAISE(ABORT, 'blocked'); END")
    con.commit()
    con.close()
    before = everything(fresh)
    assert run(fresh) == 1
    err = capsys.readouterr().err
    assert "nothing saved" in err and "$2b$" not in err and PASSWORD not in err
    assert everything(fresh) == before
