"""
P0-7 step 3: one set of password rules everywhere, bcrypt's 72-byte limit
handled without errors, and no password or hash in error messages.

The API tests use the throwaway test database; the script tests use
disposable files in tmp_path.
"""
import importlib.metadata
import re
import sqlite3
from pathlib import Path

import bcrypt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import scripts.create_admin as create_admin
import scripts.reset_password as reset_password
from app import passwords
from app.auth import hash_password, verify_password
from app.database import engine
from app.main import app
from app.passwords import PasswordRejected, check_password_rules
from scripts.backup_db import backup
from scripts.live_database import open_live_engine
from tests.db_helpers import TEST_ADMIN_PASSWORD, clear_test_database, reset_test_database
from tests.test_create_admin import add_business_data, answers, fingerprint, migrate

BACKEND = Path(__file__).resolve().parents[1]
USERNAME = "staffmember12"

REJECTED = [                                     # (password, what the message says)
    ("a" * 11, "at least 12"),
    ("a" * 73, "too long"),                      # 73 bytes
    ("é" * 37, "too long"),                      # 37 characters, 74 bytes
    ("🙂" * 19, "too long"),                     # 19 characters, 76 bytes
    (" padded password ", "start or end with a space"),
    ("ends with a tab\t", "start or end with a space"),
    (USERNAME.upper(), "same as the username"),
]
ACCEPTED = ["twelve chars", "a" * 72, "é" * 36, "🙂" * 18, "correct horse battery staple"]   # 18 emoji = 72 bytes


# ---------- the shared rules ----------
@pytest.mark.parametrize("password, reason", REJECTED)
def test_rules_reject(password, reason):
    with pytest.raises(PasswordRejected, match=reason) as e:
        check_password_rules(password, USERNAME)
    assert password.strip() not in str(e.value)  # the message never contains the password


@pytest.mark.parametrize("password", ACCEPTED)
def test_rules_accept(password):
    check_password_rules(password, USERNAME)


def test_hash_password_refuses_more_than_72_bytes_instead_of_cutting():
    with pytest.raises(PasswordRejected, match="too long"):
        hash_password("é" * 37)
    assert verify_password("é" * 36, hash_password("é" * 36))


def test_bcrypt_is_bounded_below_6():
    line = next(l for l in (BACKEND / "requirements.txt").read_text().splitlines() if l.startswith("bcrypt"))
    assert re.match(r"bcrypt>=4\.1,<6\b", line), line
    assert int(importlib.metadata.version("bcrypt").split(".")[0]) < 6


# ---------- the API ----------
@pytest.fixture()
def client():
    reset_test_database()
    with TestClient(app) as c:
        yield c
    clear_test_database()


@pytest.fixture()
def admin(client):
    r = client.post("/auth/login", data={"username": "admin", "password": TEST_ADMIN_PASSWORD})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def new_user(password, **extra):
    return {"username": USERNAME, "fullname": "Staff Member", "role": "User",
            "email": "staff@example.com", "password": password, **extra}


def usernames():
    with engine.connect() as conn:
        return [r[0] for r in conn.execute(text("SELECT username FROM users ORDER BY id"))]


@pytest.mark.parametrize("password, reason", REJECTED)
def test_api_applies_the_same_rules(client, admin, password, reason):
    r = client.post("/users", json=new_user(password), headers=admin)
    assert r.status_code == 422, r.text                           # never a 500
    assert reason in " ".join(d["msg"] for d in r.json()["detail"])
    assert password not in r.text
    assert usernames() == ["admin"]                               # nothing created


@pytest.mark.parametrize("password", ACCEPTED)
def test_api_accepts_and_the_new_user_can_log_in(client, admin, password):
    assert client.post("/users", json=new_user(password), headers=admin).status_code == 201
    assert client.post("/auth/login", data={"username": USERNAME, "password": password}).status_code == 200


@pytest.mark.parametrize("password", ["a" * 73, "é" * 100, "x" * 10_000])
def test_long_login_attempt_is_a_normal_401_not_a_500(client, password):
    """Anyone, without logging in, used to be able to cause a server error this way."""
    r = client.post("/auth/login", data={"username": "admin", "password": password})
    assert r.status_code == 401 and r.json() == {"detail": "Invalid username or password."}


def test_existing_accounts_keep_working(client):
    """Logging in isn't checked against the new rules: an account with a
    short password from before them still logs in."""
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO users (username, fullname, role, email, password_hash) "
                          "VALUES ('old6', 'Old', 'User', 'o@example.com', :h)"),
                     {"h": bcrypt.hashpw(b"abc123", bcrypt.gensalt()).decode()})
    assert client.post("/auth/login", data={"username": "old6", "password": "abc123"}).status_code == 200
    assert client.post("/auth/login", data={"username": "old6", "password": "abc1234"}).status_code == 401


P72 = "p" * 60 + "-exactly-72!"                                   # 72 bytes


@pytest.mark.parametrize("password, attempt, expected", [
    (P72, P72, 200),
    (P72, P72 + "x", 401),                                        # same first 72 bytes, one byte more
    (P72, P72 + " more text", 401),
    ("é" * 36, "é" * 36, 200),                                    # 72 bytes in 2-byte letters
    ("é" * 36, "é" * 36 + "a", 401),
    ("é" * 36, "é" * 37, 401),
])
def test_passwords_that_differ_only_after_byte_72_are_different(client, admin, password, attempt, expected):
    """bcrypt itself only reads 72 bytes; the app must not quietly treat a longer
    attempt as matching. It's a normal wrong-password 401, never a 500."""
    assert len(password.encode()) == 72 and attempt.encode()[:72] == password.encode()
    assert client.post("/users", json=new_user(password), headers=admin).status_code == 201
    r = client.post("/auth/login", data={"username": USERNAME, "password": attempt})
    assert r.status_code == expected, r.text
    if expected == 401:
        assert r.json() == {"detail": "Invalid username or password."}


def test_long_attempts_never_reach_bcrypt(monkeypatch):
    """Even against a hash that bcrypt would match on the first 72 bytes."""
    stored = bcrypt.hashpw(P72.encode(), bcrypt.gensalt()).decode()
    called = []
    real = bcrypt.checkpw
    monkeypatch.setattr(bcrypt, "checkpw", lambda *a: called.append(1) or real(*a))
    assert verify_password(P72 + "x", stored) is False and called == []
    assert verify_password(P72, stored) is True and called == [1]


# ---------- 422 responses never echo what was sent ----------
SECRET = "do-not-echo-this-password-1"


@pytest.mark.parametrize("body", [
    new_user(SECRET, email=None),                                 # a field error elsewhere
    {k: v for k, v in new_user(SECRET).items() if k != "email"},   # missing field: input was the whole body
    new_user(SECRET, role="Boss"),
    new_user(SECRET, username=""),
    new_user(SECRET[:5]),                                         # the password itself is wrong
])
def test_user_validation_errors_never_contain_the_password(client, admin, body):
    r = client.post("/users", json=body, headers=admin)
    assert r.status_code == 422
    assert SECRET[:5] not in r.text and SECRET not in r.text
    for d in r.json()["detail"]:
        assert "input" not in d and {"type", "loc", "msg"} <= d.keys()


def test_login_form_errors_never_contain_the_password(client):
    r = client.post("/auth/login", data={"password": SECRET})       # no username
    assert r.status_code == 422 and SECRET not in r.text


def test_other_validation_errors_keep_their_messages(client, admin):
    """What the frontend shows (detail[].msg) is unchanged."""
    r = client.post("/products", json={"product_name": "Eggs", "price": 4500, "quantity": 5,
                                       "date_added": "2026-11-05", "expiry_date": "2026-11-01"}, headers=admin)
    assert r.status_code == 422
    assert [d["msg"] for d in r.json()["detail"]] == ["Value error, Expiry Date cannot be before Date Added."]
    assert "input" not in r.json()["detail"][0]


# ---------- database errors never show the values being written ----------
def test_app_engine_hides_parameters():
    assert engine.hide_parameters is True


def test_failed_user_insert_shows_no_hash_or_values(client, admin):
    with engine.begin() as conn:
        conn.execute(text("CREATE TRIGGER refuse BEFORE INSERT ON users BEGIN SELECT RAISE(ABORT, 'blocked'); END"))
    try:
        with pytest.raises(Exception) as e:
            client.post("/users", json=new_user("a valid password 1"), headers=admin)
    finally:
        with engine.begin() as conn:
            conn.execute(text("DROP TRIGGER refuse"))
    shown = str(e.value) + repr(e.value.args)
    assert "parameters hidden" in shown
    assert "$2b$" not in shown and "staff@example.com" not in shown and "a valid password 1" not in shown
    assert usernames() == ["admin"]


def test_script_engines_hide_parameters(tmp_path):
    db = tmp_path / "x.db"
    migrate(db)
    e = open_live_engine(db)
    try:
        assert e.hide_parameters is True
        with e.connect() as conn:
            assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
    finally:
        e.dispose()


# ---------- the command-line tools: same rules, shared safety checks ----------
@pytest.fixture()
def live(tmp_path):
    db = tmp_path / "FoodHeroes-data" / "foodheroes.db"
    db.parent.mkdir()
    migrate(db)
    add_business_data(db)
    con = sqlite3.connect(db)
    con.execute("INSERT INTO users (username, fullname, role, email, password_hash) "
                "VALUES (?, 'Staff', 'User', 's@example.com', ?)", (USERNAME, hash_password("original password 1")))
    con.commit()
    con.close()
    return db


def run_create(db, password):
    return create_admin.main(["--database", str(db), "--username", "owner", "--fullname", "Owner",
                              "--email", "o@example.com"], prompt=answers(password, password))


def run_reset(db, password):
    return reset_password.main(["--database", str(db), "--username", USERNAME], prompt=answers(password, password))


@pytest.mark.parametrize("password, reason", REJECTED)
def test_reset_password_applies_the_same_rules(live, capsys, password, reason):
    before = fingerprint(live)
    assert run_reset(live, password) == 1
    err = capsys.readouterr().err
    assert reason in err and password.strip() not in err
    assert fingerprint(live) == before


@pytest.mark.parametrize("password", ACCEPTED)
def test_reset_password_accepts_up_to_72_bytes(live, password):
    assert run_reset(live, password) == 0
    con = sqlite3.connect(live)
    (h,) = con.execute("SELECT password_hash FROM users WHERE username = ?", (USERNAME,)).fetchone()
    con.close()
    assert verify_password(password, h)


def test_both_tools_use_the_shared_rules_and_checks():
    assert reset_password.check_new_password is passwords.check_new_password
    assert create_admin.check_new_password is passwords.check_new_password
    for tool in (reset_password, create_admin):
        assert not hasattr(tool, "MIN_LENGTH") and not hasattr(tool, "target_database") and not hasattr(tool, "live_database")


@pytest.mark.parametrize("run", [run_create, run_reset], ids=["create_admin", "reset_password"])
@pytest.mark.parametrize("suffix", ["-journal", "-wal"])
def test_both_tools_refuse_while_the_app_may_be_running(live, capsys, run, suffix):
    Path(str(live) + suffix).write_bytes(b"x")
    before = fingerprint(live)
    assert run(live, "a valid password 1") == 1
    assert "may be running" in capsys.readouterr().err and fingerprint(live) == before


@pytest.mark.parametrize("run", [run_create, run_reset], ids=["create_admin", "reset_password"])
def test_both_tools_refuse_a_backup_reached_through_a_symlink(live, tmp_path, capsys, run):
    """A link named like the live database, pointing at a backup, is still a backup."""
    res = backup(live, tmp_path / "FoodHeroes-backups")
    link = tmp_path / "looks-live.db"
    link.symlink_to(res.backup)
    before = fingerprint(res.backup)
    assert run(link, "a valid password 1") == 1
    assert "looks like a backup" in capsys.readouterr().err and fingerprint(res.backup) == before


@pytest.mark.parametrize("run", [run_create, run_reset], ids=["create_admin", "reset_password"])
def test_both_tools_refuse_an_outdated_database(tmp_path, capsys, run):
    db = tmp_path / "old.db"
    migrate(db, "0001")
    before = fingerprint(db)
    assert run(db, "a valid password 1") == 1
    assert "isn't at the latest migration" in capsys.readouterr().err and fingerprint(db) == before
