"""
Application settings that affect security, validated before the app serves
anything (P0-7).

    DATABASE_URL      required. Which database the app uses. There is no
                      default. SQLite must be a full path
                      (sqlite:////Users/you/FoodHeroes-data/foodheroes.db);
                      a relative path is refused, because it would depend on
                      the folder the backend happens to be started from.
                      PostgreSQL: postgresql+psycopg://user:password@host/db.
    SECRET_KEY        Signs login tokens. At least 32 characters, random, and
                      not a known default or placeholder. Set exactly one of
                      SECRET_KEY or SECRET_KEY_FILE.
    SECRET_KEY_FILE   Full path of a file holding the key (one line), readable
                      only by its owner (chmod 600). The usual way hosts and
                      Docker supply secrets, and keeps the key out of env files.
    TOKEN_MINUTES     optional, default 480 (an 8-hour shift). 5 to 720.
    FRONTEND_ORIGINS  optional, default http://localhost:5173. Comma-separated
                      list of exact origins allowed to call the API from a
                      browser, checked with a URL parser: http(s) scheme, valid
                      host and port, nothing else. "*" is refused. Entries are
                      normalised to the form browsers send (lower-case, default
                      port dropped). The CORS middleware uses exactly the list
                      validated at startup (app/cors.py).

Values are read from the environment each time they're needed, so nothing is
frozen at import (DATABASE_URL is read once, when the app first opens the
database: app/database.py). Importing this module (or app.auth) never requires
SECRET_KEY: the command-line tools (alembic, backup, preflight,
reset_password) don't sign tokens and keep working without it. The server's
startup calls check_configuration() and refuses to start if anything is
wrong, and signing or checking a token re-validates the key, so
authentication can't run on a missing or insecure key even if startup were
bypassed.

Error messages describe what's wrong but never include a secret's value
(a database URL's password is always shown as ***).

docs/CONFIGURATION.md explains how to set all of this, locally and on a host.
"""
import ipaddress
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

DEFAULT_TOKEN_MINUTES = 480
TOKEN_MINUTES_RANGE = (5, 720)
DEFAULT_FRONTEND_ORIGINS = "http://localhost:5173"
MIN_SECRET_LENGTH = 32
MIN_DISTINCT_CHARACTERS = 10
MAX_KEY_FILE_BYTES = 4096
SUPPORTED_DATABASES = ("sqlite", "postgresql")

# Keys published in this repository's history, and placeholder words that mean
# "this is an example value, replace it".
KNOWN_INSECURE_KEYS = {"dev-only-secret-key-change-me-before-deploying"}
PLACEHOLDER_WORDS = ("changeme", "change-me", "change_me", "replace-me", "replace_me",
                     "placeholder", "example", "your-secret", "your_secret")

GENERATE_HINT = (
    "Generate one with:\n"
    "    python3 -c \"import secrets; print(secrets.token_urlsafe(48))\"\n"
    "and set it before starting the backend: see \"Ending existing logins (rotating SECRET_KEY)\" "
    "in docs/DATABASE_RECOVERY.md."
)


class ConfigurationError(RuntimeError):
    """The app is configured unsafely. The message says what to fix (never the secret itself)."""


# ---------- DATABASE_URL ----------
def database_url() -> str:
    """The validated DATABASE_URL. Raises ConfigurationError; never falls back to a default."""
    raw = os.environ.get("DATABASE_URL")
    if raw is None or raw.strip() == "":
        raise ConfigurationError(
            "DATABASE_URL is not set, so the backend doesn't know which database to use. There is no "
            "default. Set it to the full path of the live database, e.g.\n"
            "    DATABASE_URL=sqlite:////Users/you/FoodHeroes-data/foodheroes.db\n"
            "See docs/CONFIGURATION.md.")
    if raw != raw.strip():
        raise ConfigurationError("DATABASE_URL starts or ends with spaces. Remove them.")
    try:
        url = make_url(raw)
    except (ArgumentError, ValueError):
        raise ConfigurationError("DATABASE_URL isn't a valid database URL. Use the form "
                                 "sqlite:////full/path/foodheroes.db") from None
    shown = url.render_as_string(hide_password=True)
    backend = url.get_backend_name()
    if backend not in SUPPORTED_DATABASES:
        raise ConfigurationError(f"DATABASE_URL {shown} uses {backend!r}; FoodHeroes supports "
                                 f"{' and '.join(SUPPORTED_DATABASES)}.")
    if backend == "sqlite":
        db = url.database or ""
        if db == "" or db == ":memory:" or db.startswith("file:") or url.query:
            raise ConfigurationError(f"DATABASE_URL {shown} doesn't name a database file. Use "
                                     "sqlite:////full/path/foodheroes.db (no in-memory database, no options).")
        if not os.path.isabs(db):
            raise ConfigurationError(
                f"DATABASE_URL {shown} is a relative path, so it would point at a different file depending "
                "on which folder the backend is started from. Use the full path, with four slashes, e.g.\n"
                "    DATABASE_URL=sqlite:////Users/you/FoodHeroes-data/foodheroes.db")
    return raw


def describe_database_url(raw: str) -> str:
    """For messages and logs: the URL with any password replaced by ***."""
    return make_url(raw).render_as_string(hide_password=True)


# ---------- SECRET_KEY / SECRET_KEY_FILE ----------
def _read_key_file(raw_path: str) -> str:
    """The key from SECRET_KEY_FILE. Messages name the file, never its contents."""
    path = Path(raw_path)
    if not path.is_absolute():
        raise ConfigurationError(f"SECRET_KEY_FILE must be a full path (got {raw_path!r}), e.g. "
                                 "/Users/you/FoodHeroes-data/secret_key")
    try:
        info = path.stat()
    except OSError:
        raise ConfigurationError(f"SECRET_KEY_FILE {path} doesn't exist or can't be read.\n" + GENERATE_HINT) from None
    if not stat.S_ISREG(info.st_mode):
        raise ConfigurationError(f"SECRET_KEY_FILE {path} isn't a regular file.")
    if os.name == "posix" and info.st_mode & 0o077:
        raise ConfigurationError(f"SECRET_KEY_FILE {path} can be read by other users of this computer. "
                                 f"Make it private with:\n    chmod 600 {path}")
    if info.st_size > MAX_KEY_FILE_BYTES:
        raise ConfigurationError(f"SECRET_KEY_FILE {path} is too large to be a key file.")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise ConfigurationError(f"SECRET_KEY_FILE {path} can't be read as text.") from None
    if text.endswith("\r\n"):
        text = text[:-2]
    elif text.endswith("\n"):
        text = text[:-1]
    if "\n" in text or "\r" in text:
        raise ConfigurationError(f"SECRET_KEY_FILE {path} must contain the key on a single line.")
    return text


def _configured_key() -> str | None:
    key, key_file = os.environ.get("SECRET_KEY"), os.environ.get("SECRET_KEY_FILE")
    if key is not None and key_file is not None:
        raise ConfigurationError("Both SECRET_KEY and SECRET_KEY_FILE are set, so it's unclear which key "
                                 "to use. Set only one of them.")
    if key_file is not None:
        return _read_key_file(key_file)
    return key


def signing_key() -> str:
    """The validated key from SECRET_KEY or SECRET_KEY_FILE. Raises ConfigurationError
    if it's missing or unsafe. Read on every use, so a changed key applies at once."""
    key = _configured_key()
    if key is None or key.strip() == "":
        raise ConfigurationError("SECRET_KEY is not set. The backend needs its own secret key to sign logins.\n"
                                 + GENERATE_HINT)
    if key in KNOWN_INSECURE_KEYS:
        raise ConfigurationError("SECRET_KEY is set to the default published in this repository, so anyone "
                                 "could create valid logins. Use a new random key.\n" + GENERATE_HINT)
    if any(word in key.lower() for word in PLACEHOLDER_WORDS):
        raise ConfigurationError("SECRET_KEY looks like a placeholder or example value. Use a new random key.\n"
                                 + GENERATE_HINT)
    if len(key) < MIN_SECRET_LENGTH:
        raise ConfigurationError(f"SECRET_KEY is too short: it must be at least {MIN_SECRET_LENGTH} characters.\n"
                                 + GENERATE_HINT)
    if len(set(key)) < MIN_DISTINCT_CHARACTERS:
        raise ConfigurationError("SECRET_KEY isn't random enough (too few different characters). "
                                 "Use a new random key.\n" + GENERATE_HINT)
    return key


def token_minutes() -> int:
    raw = os.environ.get("TOKEN_MINUTES", str(DEFAULT_TOKEN_MINUTES)).strip()
    low, high = TOKEN_MINUTES_RANGE
    try:
        minutes = int(raw)
    except ValueError:
        raise ConfigurationError(f"TOKEN_MINUTES must be a whole number of minutes ({low} to {high}), got {raw!r}.") from None
    if not low <= minutes <= high:
        raise ConfigurationError(f"TOKEN_MINUTES must be between {low} and {high} minutes, got {minutes}.")
    return minutes


_HOST_LABEL = re.compile(r"(?!-)[a-z0-9-]{1,63}(?<!-)")
DEFAULT_PORTS = {"http": 80, "https": 443}


def parse_origin(entry: str) -> str:
    """Validate one FRONTEND_ORIGINS entry with a real URL parser and return it in the
    exact form browsers send in the Origin header: scheme://host[:port], lower-case,
    default port omitted. Raises ConfigurationError for anything that isn't an origin."""
    def refuse(problem: str, show: bool = True):
        shown = f" {entry!r}" if show else ""
        raise ConfigurationError(f"FRONTEND_ORIGINS entry{shown} {problem}. Use the form https://example.com "
                                 "or http://localhost:5173 (scheme, host, optional port; nothing else).")

    if entry == "*" or "*" in entry:
        raise ConfigurationError("FRONTEND_ORIGINS must list exact addresses; '*' (any website) isn't allowed, "
                                 "because logins are sent with every request.")
    if any(ch.isspace() for ch in entry) or any(ord(ch) < 32 for ch in entry):
        refuse("contains spaces or control characters")
    try:
        parts = urlsplit(entry)
    except ValueError:
        refuse("isn't a valid URL")
    if "@" in parts.netloc:                                  # user:password@host
        refuse("contains a username or password", show=False)
    scheme = parts.scheme.lower()
    if scheme not in DEFAULT_PORTS:
        refuse("must start with http:// or https://")
    if parts.path or parts.query or parts.fragment or entry.endswith(("?", "#")):
        refuse("must not have a path, trailing slash, query or fragment")
    host = parts.hostname                                    # already lower-cased by urlsplit
    if not host:
        refuse("has no host name")
    try:
        port = parts.port
    except ValueError:
        refuse("has an invalid port")
    if port is not None and not 1 <= port <= 65535:
        refuse("has an invalid port")
    if ":" in host:                                          # IPv6, must be bracketed in the URL
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            refuse("has an invalid IPv6 address")
        shown_host = f"[{host}]"
    else:
        try:
            ipaddress.IPv4Address(host)
        except ValueError:
            if host.endswith(".") or not all(_HOST_LABEL.fullmatch(label) for label in host.split(".")):
                refuse("has an invalid host name")
        shown_host = host
    if port is None or port == DEFAULT_PORTS[scheme]:
        return f"{scheme}://{shown_host}"
    return f"{scheme}://{shown_host}:{port}"


def frontend_origins() -> tuple[str, ...]:
    """The validated, canonical origins allowed by CORS. Raises ConfigurationError."""
    raw = os.environ.get("FRONTEND_ORIGINS", DEFAULT_FRONTEND_ORIGINS)
    entries = [o.strip() for o in raw.split(",")]
    if not any(entries):
        raise ConfigurationError("FRONTEND_ORIGINS is empty. List the website address(es), e.g. "
                                 f"{DEFAULT_FRONTEND_ORIGINS}")
    if "" in entries:
        raise ConfigurationError("FRONTEND_ORIGINS has an empty entry (two commas in a row, or a comma at "
                                 "the start or end).")
    origins = []
    for entry in entries:
        origin = parse_origin(entry)
        if origin not in origins:
            origins.append(origin)
    return tuple(origins)


@dataclass(frozen=True)
class Settings:
    """What startup validated. The CORS middleware uses exactly these origins."""
    frontend_origins: tuple[str, ...]


def check_configuration() -> Settings:
    """Validate every setting. Called first at startup; raises ConfigurationError."""
    database_url()
    signing_key()
    token_minutes()
    return Settings(frontend_origins=frontend_origins())
