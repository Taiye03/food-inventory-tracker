"""
Application settings that affect security, validated before the app serves
anything (P0-7).

    SECRET_KEY        required. Signs login tokens. At least 32 characters,
                      random, and not a known default or placeholder.
    TOKEN_MINUTES     optional, default 480 (an 8-hour shift). 5 to 720.
    FRONTEND_ORIGINS  optional, default http://localhost:5173. Comma-separated
                      list of exact origins allowed to call the API from a
                      browser, checked with a URL parser: http(s) scheme, valid
                      host and port, nothing else. "*" is refused. Entries are
                      normalised to the form browsers send (lower-case, default
                      port dropped). The CORS middleware uses exactly the list
                      validated at startup (app/cors.py).

Values are read from the environment each time they're needed, so nothing is
frozen at import. Importing this module (or app.auth) never requires
SECRET_KEY: the command-line tools (alembic, backup, preflight,
reset_password) don't sign tokens and keep working without it. The server's
startup calls check_configuration() and refuses to start if anything is
wrong, and signing or checking a token re-validates the key, so
authentication can't run on a missing or insecure key even if startup were
bypassed.

Error messages describe what's wrong but never include a secret's value.
"""
import ipaddress
import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

DEFAULT_TOKEN_MINUTES = 480
TOKEN_MINUTES_RANGE = (5, 720)
DEFAULT_FRONTEND_ORIGINS = "http://localhost:5173"
MIN_SECRET_LENGTH = 32
MIN_DISTINCT_CHARACTERS = 10

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


def signing_key() -> str:
    """The validated SECRET_KEY. Raises ConfigurationError if it's missing or unsafe."""
    key = os.environ.get("SECRET_KEY")
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
    """Validate every security setting. Called first at startup; raises ConfigurationError."""
    signing_key()
    token_minutes()
    return Settings(frontend_origins=frontend_origins())
