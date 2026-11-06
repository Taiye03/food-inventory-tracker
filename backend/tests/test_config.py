"""
P0-7 step 1: the backend refuses to start, sign or accept logins with a
missing or unsafe SECRET_KEY, and validates TOKEN_MINUTES and FRONTEND_ORIGINS.
App tests use the throwaway test database; no real key is ever used.
"""
import secrets
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.auth import create_token
from app.config import (ConfigurationError, check_configuration, frontend_origins,
                        signing_key, token_minutes)
from app.database import engine
from app.main import app
from tests.db_helpers import TEST_ADMIN_PASSWORD, clear_test_database, reset_test_database

PUBLISHED_DEFAULT = "dev-only-secret-key-change-me-before-deploying"
GOOD_KEY = secrets.token_urlsafe(48)


@pytest.fixture()
def env(monkeypatch):
    """Start every test from a valid configuration; each test breaks one thing."""
    monkeypatch.setenv("SECRET_KEY", GOOD_KEY)
    monkeypatch.delenv("TOKEN_MINUTES", raising=False)
    monkeypatch.delenv("FRONTEND_ORIGINS", raising=False)
    return monkeypatch


# ---------- SECRET_KEY ----------
def test_valid_configuration_passes(env):
    check_configuration()
    assert signing_key() == GOOD_KEY


BAD_KEYS = {
    "published default": PUBLISHED_DEFAULT,
    "placeholder": "replace-me-with-a-long-random-string-0123456789",
    "contains changeme": "Xq9-changeme-Zk2-" + secrets.token_urlsafe(24),
    "31 characters": secrets.token_urlsafe(48)[:31],
    "long but not random": "ab" * 40,
    "blank": "   ",
    "empty": "",
}


@pytest.mark.parametrize("name", list(BAD_KEYS))
def test_unsafe_keys_are_refused_without_echoing_them(env, name):
    env.setenv("SECRET_KEY", BAD_KEYS[name])
    with pytest.raises(ConfigurationError) as e:
        check_configuration()
    message = str(e.value)
    assert "SECRET_KEY" in message and "secrets.token_urlsafe" in message   # says what to do
    if BAD_KEYS[name].strip():
        assert BAD_KEYS[name] not in message                                # never shows the value


def test_missing_key_is_refused(env):
    env.delenv("SECRET_KEY")
    with pytest.raises(ConfigurationError, match="SECRET_KEY is not set"):
        check_configuration()


def test_importing_auth_does_not_need_the_key():
    """The command-line tools import app.auth for password hashing and must work without
    SECRET_KEY. Checked in a separate Python process with no key in its environment."""
    import os
    import subprocess
    import sys
    child_env = {k: v for k, v in os.environ.items() if k != "SECRET_KEY"}
    code = ("import app.auth, app.main\n"
            "assert app.auth.verify_password('x' * 12, app.auth.hash_password('x' * 12))\n"
            "print('ok')")
    r = subprocess.run([sys.executable, "-c", code], env=child_env, capture_output=True, text=True,
                       cwd=str(__import__("pathlib").Path(__file__).resolve().parents[1]))
    assert r.returncode == 0 and r.stdout.strip() == "ok", r.stderr


# ---------- TOKEN_MINUTES ----------
@pytest.mark.parametrize("value, minutes", [(None, 480), ("5", 5), ("720", 720), (" 60 ", 60)])
def test_token_minutes_accepted(env, value, minutes):
    if value is not None:
        env.setenv("TOKEN_MINUTES", value)
    assert token_minutes() == minutes
    check_configuration()


@pytest.mark.parametrize("value", ["4", "721", "0", "-5", "abc", "8h", "1.5", ""])
def test_token_minutes_refused(env, value):
    env.setenv("TOKEN_MINUTES", value)
    with pytest.raises(ConfigurationError, match="TOKEN_MINUTES"):
        check_configuration()


def test_tokens_expire_after_token_minutes(env):
    env.setenv("TOKEN_MINUTES", "30")
    payload = jwt.decode(create_token("admin", "Admin"), GOOD_KEY, algorithms=["HS256"])
    lifetime = datetime.fromtimestamp(payload["exp"], timezone.utc) - datetime.now(timezone.utc)
    assert timedelta(minutes=29) < lifetime <= timedelta(minutes=30)


# ---------- FRONTEND_ORIGINS: parsed as URLs ----------
@pytest.mark.parametrize("value, origins", [
    (None, ("http://localhost:5173",)),
    ("https://foodheroes.example.com", ("https://foodheroes.example.com",)),
    ("http://localhost:5173, https://app.example.com:8443", ("http://localhost:5173", "https://app.example.com:8443")),
    ("http://127.0.0.1:5173", ("http://127.0.0.1:5173",)),
    ("http://[::1]:5173", ("http://[::1]:5173",)),
    ("https://xn--bcher-kva.example", ("https://xn--bcher-kva.example",)),
    # normalised to exactly what browsers send in the Origin header
    ("HTTPS://FoodHeroes.Example.COM", ("https://foodheroes.example.com",)),
    ("https://example.com:443", ("https://example.com",)),
    ("http://example.com:80", ("http://example.com",)),
    ("http://localhost:5173,http://localhost:5173", ("http://localhost:5173",)),
])
def test_origins_accepted(env, value, origins):
    if value is not None:
        env.setenv("FRONTEND_ORIGINS", value)
    assert frontend_origins() == origins
    assert check_configuration().frontend_origins == origins


REFUSED_ORIGINS = {
    "any origin": "*",
    "wildcard subdomain": "https://*.example.com",
    "wildcard in a list": "http://a.com,*",
    "no scheme": "localhost:5173",
    "other scheme": "ftp://example.com",
    "javascript scheme": "javascript:alert(1)",
    "the null origin": "null",
    "trailing slash": "http://localhost:5173/",
    "path": "https://example.com/app",
    "query": "https://example.com?next=/",
    "empty query": "https://example.com?",
    "fragment": "https://example.com#top",
    "empty fragment": "https://example.com#",
    "credentials": "https://admin:hunter2-secret@example.com",
    "username only": "https://admin@example.com",
    "no host": "https://",
    "no host, with path": "http:///x",
    "port only": "http://:5173",
    "port 0": "http://localhost:0",
    "port too big": "http://localhost:99999",
    "port not a number": "http://localhost:abc",
    "space in host": "https://exa mple.com",
    "underscore in host": "https://my_host.example.com",
    "label starting with hyphen": "https://-bad.example.com",
    "empty label": "https://example..com",
    "trailing dot": "https://example.com.",
    "bad IPv6": "http://[::zz]:5173",
    "unbracketed IPv6": "http://::1:5173",
    "backslash trick": "https://example.com\\@evil.com",
    "empty entry": "http://localhost:5173,,https://example.com",
    "only commas": " , ",
}


@pytest.mark.parametrize("name", list(REFUSED_ORIGINS))
def test_origins_refused(env, name):
    env.setenv("FRONTEND_ORIGINS", REFUSED_ORIGINS[name])
    with pytest.raises(ConfigurationError, match="FRONTEND_ORIGINS") as e:
        check_configuration()
    assert "hunter2-secret" not in str(e.value)          # credentials are never echoed


# ---------- CORS uses exactly what startup validated ----------
def preflight(client, origin):
    r = client.options("/orders", headers={"Origin": origin, "Access-Control-Request-Method": "POST"})
    return r.headers.get("access-control-allow-origin")


def simple(client, origin):
    return client.get("/", headers={"Origin": origin}).headers.get("access-control-allow-origin")


def test_cors_uses_the_configuration_at_startup_not_at_import(env):
    """app.main was imported long before this test, with the default origin.
    Changing FRONTEND_ORIGINS before startup must change what CORS allows."""
    env.setenv("FRONTEND_ORIGINS", "https://shop.example.com")
    reset_test_database()
    with TestClient(app) as c:
        assert preflight(c, "https://shop.example.com") == "https://shop.example.com"
        assert simple(c, "https://shop.example.com") == "https://shop.example.com"
        assert preflight(c, "http://localhost:5173") is None          # the import-time default is NOT kept
        assert simple(c, "http://localhost:5173") is None
    clear_test_database()


def test_cors_and_startup_validation_use_the_same_list(env):
    env.setenv("FRONTEND_ORIGINS", "HTTP://LOCALHOST:5173, https://app.example.com:443")
    reset_test_database()
    with TestClient(app) as c:
        assert app.state.frontend_origins == check_configuration().frontend_origins \
            == ("http://localhost:5173", "https://app.example.com")
        assert preflight(c, "http://localhost:5173") == "http://localhost:5173"
        assert preflight(c, "https://app.example.com") == "https://app.example.com"
        assert preflight(c, "https://app.example.com:8443") is None   # other port: different origin
        assert preflight(c, "https://evil.example.com") is None
    clear_test_database()


def test_cors_allows_nothing_until_startup_succeeds(env):
    reset_test_database()
    assert app.state.frontend_origins == ()                            # before any startup
    c = TestClient(app)                                                 # no "with": startup never runs
    assert preflight(c, "http://localhost:5173") is None
    env.setenv("FRONTEND_ORIGINS", "http://localhost:5173/")            # invalid: startup refuses
    with pytest.raises(ConfigurationError):
        with TestClient(app):
            pass
    assert app.state.frontend_origins == ()                            # nothing was accepted
    assert preflight(TestClient(app), "http://localhost:5173") is None
    clear_test_database()


def test_cors_allows_nothing_if_a_later_startup_check_fails(env):
    """Valid origins, but the database isn't ready: startup fails, so CORS stays closed."""
    from app.db_check import DatabaseNotReady
    reset_test_database(revision=None)                   # tables but no migration revision
    with pytest.raises(DatabaseNotReady):
        with TestClient(app):
            pass
    assert app.state.frontend_origins == ()
    assert preflight(TestClient(app), "http://localhost:5173") is None
    clear_test_database()


def test_cors_allows_nothing_after_shutdown(env):
    reset_test_database()
    with TestClient(app) as c:
        assert preflight(c, "http://localhost:5173") == "http://localhost:5173"
    assert app.state.frontend_origins == ()
    assert preflight(TestClient(app), "http://localhost:5173") is None
    clear_test_database()


def test_cors_response_headers_unchanged_for_a_valid_origin(env):
    """Same behaviour as before for the default configuration."""
    reset_test_database()
    with TestClient(app) as c:
        r = c.options("/orders", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "DELETE",
                                          "Access-Control-Request-Headers": "authorization,content-type"})
        assert r.status_code == 200
        assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
        assert r.headers["access-control-allow-credentials"] == "true"
        assert "DELETE" in r.headers["access-control-allow-methods"]
        assert "authorization" in r.headers["access-control-allow-headers"].lower()
        assert simple(c, "http://localhost:5173") == "http://localhost:5173"
    clear_test_database()


# ---------- the running app ----------
def table_names():
    with engine.connect() as conn:
        return {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}


@pytest.mark.parametrize("breakage", [("SECRET_KEY", None), ("SECRET_KEY", PUBLISHED_DEFAULT),
                                      ("TOKEN_MINUTES", "100000"), ("FRONTEND_ORIGINS", "*")])
def test_app_refuses_to_start_with_unsafe_configuration(env, breakage):
    name, value = breakage
    reset_test_database()
    with engine.connect() as conn:
        users_before = conn.execute(text("SELECT COUNT(*) FROM users")).scalar()
    if value is None:
        env.delenv(name)
    else:
        env.setenv(name, value)
    with pytest.raises(ConfigurationError, match=name):
        with TestClient(app):
            pass
    with engine.connect() as conn:                       # checked before anything else ran:
        assert conn.execute(text("SELECT COUNT(*) FROM users")).scalar() == users_before   # no admin seeded
    clear_test_database()


def test_token_signed_with_the_published_default_key_is_rejected(env):
    reset_test_database()
    with TestClient(app) as c:
        forged = jwt.encode({"sub": "admin", "role": "Admin", "exp": 9999999999}, PUBLISHED_DEFAULT, algorithm="HS256")
        assert c.get("/users", headers={"Authorization": f"Bearer {forged}"}).status_code == 401
        real = c.post("/auth/login", data={"username": "admin", "password": TEST_ADMIN_PASSWORD}).json()["access_token"]
        assert c.get("/users", headers={"Authorization": f"Bearer {real}"}).status_code == 200
    clear_test_database()


def test_rotating_the_key_ends_existing_logins(env):
    reset_test_database()
    with TestClient(app) as c:
        token = c.post("/auth/login", data={"username": "admin", "password": TEST_ADMIN_PASSWORD}).json()["access_token"]
        assert c.get("/orders", headers={"Authorization": f"Bearer {token}"}).status_code == 200
        env.setenv("SECRET_KEY", secrets.token_urlsafe(48))          # key read on every request
        assert c.get("/orders", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    clear_test_database()


def test_authentication_stops_if_the_key_disappears_after_startup(env):
    """Even past the startup check, no token is signed or accepted without a safe key."""
    reset_test_database()
    with TestClient(app, raise_server_exceptions=False) as c:
        token = c.post("/auth/login", data={"username": "admin", "password": TEST_ADMIN_PASSWORD}).json()["access_token"]
        env.setenv("SECRET_KEY", PUBLISHED_DEFAULT)
        assert c.post("/auth/login", data={"username": "admin", "password": TEST_ADMIN_PASSWORD}).status_code == 500
        assert c.get("/orders", headers={"Authorization": f"Bearer {token}"}).status_code == 500
        env.delenv("SECRET_KEY")
        assert c.get("/orders", headers={"Authorization": f"Bearer {token}"}).status_code == 500
    with pytest.raises(ConfigurationError):
        create_token("admin", "Admin")
    clear_test_database()
