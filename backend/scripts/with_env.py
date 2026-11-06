"""
Run a command with FoodHeroes settings loaded from an env file that lives
OUTSIDE the repository (docs/CONFIGURATION.md).

    python -m scripts.with_env ~/FoodHeroes-data/foodheroes.env -- python -m uvicorn app.main:app
    python -m scripts.with_env ~/FoodHeroes-data/foodheroes.env -- alembic upgrade head
    python -m scripts.with_env ~/FoodHeroes-data/foodheroes.env -- python -m scripts.db_preflight

Why not `source` the file or use `uvicorn --env-file`? Those quietly let a
value already exported in your shell (an old DATABASE_URL, say) win over the
file, run the file as shell code, and accept typos without complaint. This
tool, before running anything:

  - refuses an env file inside the repository (it could be committed), or one
    that other users of the computer can read (chmod 600 fixes that)
  - reads only plain KEY=value lines: no shell code, no $VARIABLES, no ~
  - refuses unknown or repeated settings (a typo like SECERT_KEY is an error,
    not a silently missing key)
  - refuses a setting your shell already has with a DIFFERENT value, so the
    file can never be silently overridden
  - validates every setting exactly as the backend does at startup
    (app/config.py), so a bad value is reported before anything starts

It prints which settings it loaded, never a secret's value, and then runs the
command in place of itself (Ctrl-C reaches the command as usual).
"""
import argparse
import os
import re
import stat
import sys
from pathlib import Path

from app.config import ConfigurationError, check_configuration, describe_database_url

REPOSITORY = Path(__file__).resolve().parents[2]
KNOWN_SETTINGS = ("DATABASE_URL", "SECRET_KEY", "SECRET_KEY_FILE", "TOKEN_MINUTES",
                  "FRONTEND_ORIGINS", "LOW_STOCK_THRESHOLD")
SECRET_SETTINGS = ("SECRET_KEY",)
_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SHELL_EXPANSION = re.compile(r"\$[A-Za-z_{(]|^~")   # $HOME, ${X}, $(cmd), ~/...


class EnvFileError(Exception):
    """The env file can't be used as it is; nothing has been run."""


def check_env_file(raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_file():
        raise EnvFileError(f"No env file at {path}.")
    path = path.resolve()
    if path == REPOSITORY or REPOSITORY in path.parents:
        raise EnvFileError(f"{path} is inside the repository, where it could be committed. Keep the real "
                           "settings outside it, e.g. ~/FoodHeroes-data/foodheroes.env "
                           "(backend/.env.example is only a template).")
    if os.name == "posix" and path.stat().st_mode & 0o077:
        raise EnvFileError(f"{path} can be read by other users of this computer. Make it private with:\n"
                           f"    chmod 600 {path}")
    if not stat.S_ISREG(path.stat().st_mode):
        raise EnvFileError(f"{path} isn't a regular file.")
    return path


def parse_env_file(text: str, name: str = "env file") -> dict[str, str]:
    """KEY=value lines. Blank lines and lines starting with # are ignored. A value
    may be wrapped in matching single or double quotes; nothing is expanded."""
    values: dict[str, str] = {}
    for n, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        where = f"{name}, line {n}"
        if stripped.startswith("export "):
            raise EnvFileError(f"{where}: write KEY=value without 'export'.")
        key, sep, value = stripped.partition("=")
        key = key.strip()
        if not sep or not _KEY.fullmatch(key):
            raise EnvFileError(f"{where}: expected KEY=value.")      # the line itself isn't shown: it may hold a secret
        if key not in KNOWN_SETTINGS:
            raise EnvFileError(f"{where}: unknown setting {key!r}. Known settings: {', '.join(KNOWN_SETTINGS)}.")
        if key in values:
            raise EnvFileError(f"{where}: {key} is set more than once.")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif value[:1] in "\"'":
            raise EnvFileError(f"{where}: {key} has an opening quote with no matching closing quote.")
        if key not in SECRET_SETTINGS and _SHELL_EXPANSION.search(value):
            raise EnvFileError(f"{where}: {key} looks like it uses a shell variable or ~. Nothing is expanded "
                               "here; write the value out in full (e.g. /Users/you/...).")
        values[key] = value
    return values


def merged_environment(file_values: dict[str, str], current: dict[str, str]) -> dict[str, str]:
    for key, value in file_values.items():
        if key in current and current[key] != value:
            raise EnvFileError(f"{key} is already set in this shell to a different value than in the env file, "
                               f"so it's unclear which to use. Run `unset {key}` (or open a new terminal) and try again.")
    if "SECRET_KEY_FILE" in file_values and "SECRET_KEY" in current:
        raise EnvFileError("The env file sets SECRET_KEY_FILE but this shell has SECRET_KEY set. "
                           "Run `unset SECRET_KEY` and try again.")
    if "SECRET_KEY" in file_values and "SECRET_KEY_FILE" in current:
        raise EnvFileError("The env file sets SECRET_KEY but this shell has SECRET_KEY_FILE set. "
                           "Run `unset SECRET_KEY_FILE` and try again.")
    return {**current, **file_values}


def validate(environment: dict[str, str]) -> None:
    """Run the backend's own startup checks against `environment`."""
    saved = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update(environment)
        check_configuration()
        if "LOW_STOCK_THRESHOLD" in environment and not environment["LOW_STOCK_THRESHOLD"].isdigit():
            raise ConfigurationError("LOW_STOCK_THRESHOLD must be a whole number.")
    finally:
        os.environ.clear()
        os.environ.update(saved)


def summary(path: Path, environment: dict[str, str]) -> str:
    lines = [f"Settings from {path}:", f"  DATABASE_URL     {describe_database_url(environment['DATABASE_URL'])}"]
    if "SECRET_KEY_FILE" in environment:
        lines.append(f"  SECRET_KEY_FILE  {environment['SECRET_KEY_FILE']}  (key not shown)")
    else:
        lines.append("  SECRET_KEY       set (not shown)")
    for key in ("TOKEN_MINUTES", "FRONTEND_ORIGINS", "LOW_STOCK_THRESHOLD"):
        lines.append(f"  {key:<16} {environment.get(key, '(default)')}")
    return "\n".join(lines)


def prepare(env_file: str, current: dict[str, str]) -> tuple[Path, dict[str, str]]:
    path = check_env_file(env_file)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise EnvFileError(f"{path} can't be read as text.") from None
    environment = merged_environment(parse_env_file(text, path.name), current)
    try:
        validate(environment)
    except ConfigurationError as e:
        raise EnvFileError(f"{path} isn't ready to use:\n{e}") from None
    return path, environment


def main(argv=None, execute=os.execvpe) -> int:
    p = argparse.ArgumentParser(description="Run a command with settings from an env file outside the repository.")
    p.add_argument("env_file", help="e.g. ~/FoodHeroes-data/foodheroes.env")
    p.add_argument("command", nargs=argparse.REMAINDER, help="-- then the command, e.g. -- python -m uvicorn app.main:app")
    args = p.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        p.error("give the command to run after --, e.g. -- python -m uvicorn app.main:app")
    try:
        path, environment = prepare(args.env_file, dict(os.environ))
    except EnvFileError as e:
        print(f"Not started: {e}", file=sys.stderr)
        return 1
    print(summary(path, environment), file=sys.stderr)
    sys.stderr.flush()
    try:
        execute(command[0], command, environment)   # replaces this process; only returns in tests
    except FileNotFoundError:
        print(f"Not started: command {command[0]!r} not found. Is the virtual environment active?", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
