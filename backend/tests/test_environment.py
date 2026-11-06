"""
P0-7 step 4: settings come only from the environment, are validated, and
never fall back to a built-in secret or an unintended database; the env-file
runner (scripts/with_env.py); the .env.example template; .gitignore.

Every database and settings file here is a disposable file in tmp_path, and
every key is a random one made for the test. Nothing reads the real ones.
"""
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

import scripts.with_env as with_env
from app.config import ConfigurationError, check_configuration, database_url, describe_database_url, signing_key
from app.main import app
from scripts.db_common import DatabaseToolError, default_db_path
from scripts.with_env import EnvFileError, parse_env_file

BACKEND = Path(__file__).resolve().parents[1]
REPOSITORY = BACKEND.parent
SETTINGS = ("DATABASE_URL", "SECRET_KEY", "SECRET_KEY_FILE", "TOKEN_MINUTES", "FRONTEND_ORIGINS", "LOW_STOCK_THRESHOLD")


def clean_env(**extra):
    """The current environment without any FoodHeroes setting, plus `extra`."""
    env = {k: v for k, v in os.environ.items() if k not in SETTINGS}
    env["PYTHONPATH"] = str(BACKEND)
    env.update(extra)
    return env


def key_file(tmp_path, key=None, mode=0o600, name="secret_key"):
    key = key or secrets.token_urlsafe(48)
    path = tmp_path / name
    path.write_text(key + "\n")
    path.chmod(mode)
    return path, key


# ---------- DATABASE_URL: required, full path, no fallback ----------
@pytest.mark.parametrize("value, reason", [
    (None, "is not set"),
    ("", "is not set"),
    ("   ", "is not set"),
    ("sqlite:///./foodheroes.db", "relative path"),
    ("sqlite:///foodheroes.db", "relative path"),
    ("sqlite://", "doesn't name a database file"),
    ("sqlite:///:memory:", "doesn't name a database file"),
    ("sqlite:////tmp/x.db?mode=ro", "doesn't name a database file"),
    (" sqlite:////tmp/x.db", "spaces"),
    ("mysql://user:topsecret@localhost/foodheroes", "supports sqlite and postgresql"),
    ("not a url", "isn't a valid database URL"),
])
def test_database_url_is_refused(monkeypatch, value, reason):
    if value is None:
        monkeypatch.delenv("DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("DATABASE_URL", value)
    with pytest.raises(ConfigurationError, match=reason) as e:
        database_url()
    assert "topsecret" not in str(e.value)


@pytest.mark.parametrize("value", ["sqlite:////tmp/fh/foodheroes.db", "postgresql+psycopg://fh:pw@db.internal:5432/foodheroes"])
def test_database_url_is_accepted(monkeypatch, value):
    monkeypatch.setenv("DATABASE_URL", value)
    assert database_url() == value


def test_database_password_is_never_shown():
    assert describe_database_url("postgresql+psycopg://fh:Sup3r-secret@db/foodheroes") == "postgresql+psycopg://fh:***@db/foodheroes"


@pytest.mark.parametrize("value", [None, "sqlite:///./foodheroes.db"])
def test_app_refuses_to_start_and_opens_no_database(monkeypatch, tmp_path, value):
    monkeypatch.chdir(tmp_path)
    if value is None:
        monkeypatch.delenv("DATABASE_URL")
    else:
        monkeypatch.setenv("DATABASE_URL", value)
    with pytest.raises(ConfigurationError, match="DATABASE_URL"):
        with TestClient(app):
            pass
    assert list(tmp_path.iterdir()) == []                         # no ./foodheroes.db appeared


def test_fresh_process_has_no_default_database(tmp_path):
    """Importing the app or the tools needs no settings, but nothing opens a database
    until DATABASE_URL is set: there's no ./foodheroes.db fallback anywhere."""
    code = ("import app.main, scripts.create_admin, scripts.reset_password, scripts.import_ledger\n"
            "from app.database import get_engine\n"
            "from app.config import ConfigurationError\n"
            "try:\n    get_engine()\nexcept ConfigurationError as e:\n    print('REFUSED', 'DATABASE_URL is not set' in str(e))\n")
    r = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=clean_env(), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "REFUSED True"
    assert list(tmp_path.iterdir()) == []


def test_admin_tools_work_without_any_settings(tmp_path):
    """create_admin is told the database explicitly and signs nothing."""
    db = tmp_path / "foodheroes.db"
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    cfg.attributes["skip_logging_config"] = True
    command.upgrade(cfg, "head")
    code = ("import sys\nfrom scripts.create_admin import main\n"
            f"sys.exit(main(['--database', {str(db)!r}, '--username', 'owner', '--fullname', 'Owner', "
            "'--email', 'o@example.com'], prompt=lambda t: 'environment test pw 1'))\n")
    r = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=clean_env(), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("value, reason", [(None, "Give the database file's path, or set DATABASE_URL"),
                                           ("sqlite:///./foodheroes.db", "relative path"),
                                           ("postgresql://fh:pw@db/foodheroes", "only handle SQLite")])
def test_backup_and_preflight_tools_need_a_path_or_database_url(monkeypatch, tmp_path, value, reason):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "foodheroes.db").write_bytes(b"")                   # a decoy the old default would have used
    if value is None:
        monkeypatch.delenv("DATABASE_URL")
    else:
        monkeypatch.setenv("DATABASE_URL", value)
    with pytest.raises(DatabaseToolError, match=reason):
        default_db_path()


def test_backup_tool_uses_database_url_when_valid(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'live.db'}")
    assert default_db_path() == tmp_path / "live.db"


def test_alembic_refuses_a_relative_database_url(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./foodheroes.db")
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.attributes["skip_logging_config"] = True
    with pytest.raises(Exception, match="relative path"):
        command.upgrade(cfg, "head")
    assert list(tmp_path.iterdir()) == []


# ---------- SECRET_KEY_FILE ----------
@pytest.fixture()
def no_key(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("SECRET_KEY_FILE", raising=False)
    return monkeypatch


def test_key_from_a_private_file(no_key, tmp_path):
    path, key = key_file(tmp_path)
    no_key.setenv("SECRET_KEY_FILE", str(path))
    assert signing_key() == key                                     # trailing newline removed


def test_key_file_is_read_on_each_use(no_key, tmp_path):
    path, first = key_file(tmp_path)
    no_key.setenv("SECRET_KEY_FILE", str(path))
    assert signing_key() == first
    second = secrets.token_urlsafe(48)
    path.write_text(second)
    assert signing_key() == second


@pytest.mark.parametrize("problem, reason", [
    ("readable by others", "can be read by other users"),
    ("relative", "must be a full path"),
    ("missing", "doesn't exist"),
    ("directory", "isn't a regular file"),
    ("two lines", "single line"),
    ("placeholder", "placeholder"),
    ("short", "too short"),
    ("empty", "is not set"),
])
def test_key_file_is_refused(no_key, tmp_path, problem, reason):
    if os.name != "posix" and problem == "readable by others":
        pytest.skip("POSIX permissions only")
    path, key = key_file(tmp_path, key={"two lines": secrets.token_urlsafe(48) + "\n" + secrets.token_urlsafe(48),
                                        "placeholder": "replace-me-" + secrets.token_urlsafe(40),
                                        "short": secrets.token_urlsafe(10),
                                        "empty": " "}.get(problem),
                         mode=0o644 if problem == "readable by others" else 0o600)
    setting = {"relative": "secret_key", "missing": str(tmp_path / "nope"), "directory": str(tmp_path)}.get(problem, str(path))
    no_key.setenv("SECRET_KEY_FILE", setting)
    with pytest.raises(ConfigurationError, match=reason) as e:
        signing_key()
    for line in key.splitlines():
        if len(line.strip()) > 3:
            assert line not in str(e.value)                         # the message never contains the key


def test_key_and_key_file_together_are_refused(no_key, tmp_path):
    path, key = key_file(tmp_path)
    no_key.setenv("SECRET_KEY_FILE", str(path))
    no_key.setenv("SECRET_KEY", key)                                 # even the same key: ambiguous
    with pytest.raises(ConfigurationError, match="Set only one"):
        signing_key()


# ---------- scripts/with_env.py ----------
def env_file(tmp_path, lines, mode=0o600, name="foodheroes.env"):
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n")
    path.chmod(mode)
    return path


@pytest.fixture()
def settings(tmp_path):
    """A complete, valid settings file outside the repository, with a decoy key file."""
    kf, key = key_file(tmp_path)
    db = tmp_path / "data" / "foodheroes.db"
    f = env_file(tmp_path, ["# my settings", f"DATABASE_URL=sqlite:///{db}", f"SECRET_KEY_FILE={kf}",
                            "TOKEN_MINUTES='60'", 'FRONTEND_ORIGINS="http://localhost:5173"'])
    return f, key, db


SHOW = ("import json, os; print(json.dumps({k: os.environ.get(k) for k in "
        "('DATABASE_URL', 'SECRET_KEY_FILE', 'TOKEN_MINUTES', 'FRONTEND_ORIGINS')}) + '|' + str('SECRET_KEY' in os.environ))")


def run_with(path, *cmd, env=None):
    return subprocess.run([sys.executable, "-m", "scripts.with_env", str(path), "--", *cmd], cwd=BACKEND,
                          env=env or clean_env(), capture_output=True, text=True)


def test_runs_the_command_with_the_settings_and_never_shows_the_key(settings):
    f, key, db = settings
    r = run_with(f, sys.executable, "-c", SHOW)
    assert r.returncode == 0, r.stderr
    shown, has_inline_key = r.stdout.strip().split("|")
    assert json.loads(shown) == {"DATABASE_URL": f"sqlite:///{db}", "SECRET_KEY_FILE": str(f.parent / "secret_key"),
                                 "TOKEN_MINUTES": "60", "FRONTEND_ORIGINS": "http://localhost:5173"}
    assert has_inline_key == "False"
    assert key not in r.stdout + r.stderr
    assert "key not shown" in r.stderr and f"sqlite:///{db}" in r.stderr


def test_an_inline_key_is_passed_on_but_not_shown(tmp_path):
    key = secrets.token_urlsafe(48)
    f = env_file(tmp_path, [f"DATABASE_URL=sqlite:///{tmp_path / 'x.db'}", f"SECRET_KEY={key}"])
    r = run_with(f, sys.executable, "-c", "import os; print(len(os.environ['SECRET_KEY']))")
    assert r.returncode == 0 and r.stdout.strip() == str(len(key))
    assert key not in r.stderr and "set (not shown)" in r.stderr


def test_postgres_password_is_hidden_in_the_summary(tmp_path):
    kf, _ = key_file(tmp_path)
    f = env_file(tmp_path, ["DATABASE_URL=postgresql+psycopg://fh:Hunter2-pw@db.internal/foodheroes", f"SECRET_KEY_FILE={kf}"])
    r = run_with(f, sys.executable, "-c", "pass")
    assert r.returncode == 0, r.stderr
    assert "Hunter2-pw" not in r.stderr and "fh:***@db.internal" in r.stderr


@pytest.mark.parametrize("lines, reason", [
    (["DATABASE_URL=sqlite:///./foodheroes.db"], "relative path"),
    (["SECRET_KEY_FILE=/tmp/x"], "DATABASE_URL is not set"),
    (["SECERT_KEY=abc"], "unknown setting 'SECERT_KEY'"),
    (["DATABASE_URL=sqlite:////a.db", "DATABASE_URL=sqlite:////b.db"], "set more than once"),
    (["export DATABASE_URL=sqlite:////a.db"], "without 'export'"),
    (["DATABASE_URL"], "expected KEY=value"),
    (["DATABASE_URL=sqlite:///$HOME/foodheroes.db"], "shell variable"),
    (["SECRET_KEY_FILE=~/FoodHeroes-data/secret_key"], "shell variable or ~"),
    (["DATABASE_URL=\"sqlite:////a.db"], "no matching closing quote"),
    (["DATABASE_URL=sqlite:////tmp/fh.db", f"SECRET_KEY={secrets.token_urlsafe(48)}", "TOKEN_MINUTES=9999"],
     "TOKEN_MINUTES must be between"),
])
def test_bad_settings_files_are_refused_before_running_anything(tmp_path, lines, reason):
    marker = tmp_path / "ran"
    f = env_file(tmp_path, lines)
    r = run_with(f, sys.executable, "-c", f"open({str(marker)!r}, 'w')")
    assert r.returncode == 1 and reason in r.stderr, r.stderr
    assert not marker.exists()                                      # the command never ran


def test_line_with_a_secret_isnt_echoed_when_malformed(tmp_path):
    key = secrets.token_urlsafe(48)
    f = env_file(tmp_path, [f"SECRET_KEY {key}"])                   # missing '='
    r = run_with(f, sys.executable, "-c", "pass")
    assert r.returncode == 1 and key not in r.stderr


def test_refuses_a_file_other_users_can_read(settings):
    if os.name != "posix":
        pytest.skip("POSIX permissions only")
    f, _, _ = settings
    f.chmod(0o644)
    r = run_with(f, sys.executable, "-c", "pass")
    assert r.returncode == 1 and "chmod 600" in r.stderr


def test_refuses_a_settings_file_inside_the_repository(settings, tmp_path, monkeypatch):
    f, _, _ = settings
    monkeypatch.setattr(with_env, "REPOSITORY", tmp_path)          # pretend tmp_path is the repository
    with pytest.raises(EnvFileError, match="inside the repository"):
        with_env.check_env_file(str(f))


def test_the_template_itself_is_refused():
    with pytest.raises(EnvFileError, match="inside the repository"):
        with_env.check_env_file(str(BACKEND / ".env.example"))


def test_a_different_value_already_in_the_shell_is_refused(settings, tmp_path):
    f, _, db = settings
    r = run_with(f, sys.executable, "-c", "pass", env=clean_env(DATABASE_URL=f"sqlite:///{tmp_path / 'other.db'}"))
    assert r.returncode == 1 and "DATABASE_URL is already set in this shell to a different value" in r.stderr
    r = run_with(f, sys.executable, "-c", "pass", env=clean_env(DATABASE_URL=f"sqlite:///{db}"))
    assert r.returncode == 0                                        # the same value is fine


def test_a_key_in_the_shell_and_a_key_file_in_the_settings_is_refused(settings):
    f, _, _ = settings
    r = run_with(f, sys.executable, "-c", "pass", env=clean_env(SECRET_KEY=secrets.token_urlsafe(48)))
    assert r.returncode == 1 and "unset SECRET_KEY" in r.stderr


def test_validation_leaves_this_process_environment_unchanged(settings):
    f, _, _ = settings
    before = dict(os.environ)
    with_env.prepare(str(f), {})
    assert dict(os.environ) == before
    with pytest.raises(EnvFileError):
        with_env.prepare(str(env_file(f.parent, ["TOKEN_MINUTES=1"], name="bad.env")), {})
    assert dict(os.environ) == before


def test_missing_command_and_unknown_command(settings, capsys, monkeypatch):
    f, _, _ = settings
    for k in SETTINGS:
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(SystemExit):
        with_env.main([str(f)])
    def missing(*_):
        raise FileNotFoundError
    assert with_env.main([str(f), "--", "fh-no-such-command"], execute=missing) == 1
    assert "not found" in capsys.readouterr().err


def test_runs_the_backend_startup_checks(settings, monkeypatch):
    """What with_env accepts, the backend's own check_configuration accepts too."""
    f, _, _ = settings
    _, environment = with_env.prepare(str(f), {})
    for k in SETTINGS:
        monkeypatch.delenv(k, raising=False)
    for k, v in environment.items():
        monkeypatch.setenv(k, v)
    check_configuration()


# ---------- the template ----------
EXAMPLE = BACKEND / ".env.example"


def test_template_lists_every_setting_with_safe_placeholders():
    text = EXAMPLE.read_text()
    values = parse_env_file(text, ".env.example")
    for name in SETTINGS:
        assert re.search(rf"^#?\s*{name}=", text, re.M), name     # every setting documented
    assert "SECRET_KEY" not in values                              # no key value, only a commented hint
    assert not Path(values["SECRET_KEY_FILE"]).exists()
    assert not Path(database_url_path(values["DATABASE_URL"])).parent.exists()
    assert "REPLACE" in values["DATABASE_URL"] and "REPLACE" in values["SECRET_KEY_FILE"]


def database_url_path(url):
    return url[len("sqlite:///"):]


def test_documented_settings_match_the_known_list():
    guide = (REPOSITORY / "docs" / "CONFIGURATION.md").read_text()
    for name in SETTINGS:
        assert f"`{name}`" in guide, name
    assert set(with_env.KNOWN_SETTINGS) == set(SETTINGS)


# ---------- nothing secret written into the repository's text ----------
def test_no_usable_key_appears_in_docs_or_the_template(monkeypatch):
    """Every long token-like string in the docs and the template must be refused as a key."""
    files = [EXAMPLE, REPOSITORY / "README.md", *sorted((REPOSITORY / "docs").glob("*.md"))]
    candidates = {t for f in files if f.exists() for t in re.findall(r"[A-Za-z0-9_\-]{32,}", f.read_text())
                  if not re.fullmatch(r"[a-z]+(-[a-z]+)+", t)}       # section links like #creating-the-first-administrator
    monkeypatch.delenv("SECRET_KEY_FILE", raising=False)
    for token in candidates:
        monkeypatch.setenv("SECRET_KEY", token)
        with pytest.raises(ConfigurationError):
            signing_key()


# ---------- .gitignore ----------
GITIGNORE = REPOSITORY / ".gitignore"


@pytest.mark.skipif(shutil.which("git") is None or not GITIGNORE.exists(), reason="needs git and the repository's .gitignore")
@pytest.mark.parametrize("path, ignored", [
    (".env", True), ("backend/.env", True), ("backend/.env.local", True), ("backend/.env.production", True),
    ("foodheroes.env", True), ("backend/foodheroes.env", True), ("secret_key", True), ("backend/secret_key", True),
    ("server.key", True), ("cert.pem", True), ("backend/foodheroes.db", True), ("backend/backups/x.db", True),
    ("backend/.env.example", False), ("backend/app/config.py", False), ("docs/CONFIGURATION.md", False),
])
def test_gitignore(tmp_path, path, ignored):
    shutil.copy(GITIGNORE, tmp_path / ".gitignore")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    r = subprocess.run(["git", "-C", str(tmp_path), "check-ignore", "-q", "--no-index", path])
    assert (r.returncode == 0) is ignored
