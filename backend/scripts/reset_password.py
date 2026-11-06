"""
Reset an existing user's password in a FoodHeroes database.

Run from backend/ with the app STOPPED, after a fresh backup:

    python -m scripts.reset_password --database ~/FoodHeroes-data/foodheroes.db --username admin

You're asked for the new password twice. It isn't shown as you type, and it
never appears on screen, in your shell history, in logs or in error messages.

What it changes: only that user's password_hash. The user keeps the same id,
username, role and email, and no other table or row is touched (nothing in
the database refers to users, so orders, stock and payments can't be affected).

What it refuses (and changes nothing):
  - no --database or --username given (there is no default database)
  - a path that doesn't exist or isn't an SQLite database (never creates one)
  - a backup: any file with a backup manifest (<file>.json) beside it
  - a database that isn't at the latest migration, which includes every
    pre-migration database and backup (see app/db_check.py)
  - a journal file beside the database (the app may be running)
  - a username that doesn't exist (it never creates accounts)
  - passwords that don't match, are shorter than 12 characters, or equal the username

Note: existing login tokens stay valid until they expire (8 hours). To cut
them off immediately, restart the backend with a new SECRET_KEY
(docs/DATABASE_RECOVERY.md, "Resetting a password").
"""
import argparse
import getpass
import sys
from pathlib import Path

from sqlalchemy import create_engine, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from app.auth import hash_password, verify_password
from app.database import enforce_foreign_keys
from app.db_check import DatabaseNotReady, check_database_revision
from app.models import User
from scripts.db_common import is_sqlite_file

MIN_LENGTH = 12


class ResetRefused(Exception):
    """Something isn't safe or isn't right; nothing has been changed."""


def target_database(raw: str) -> Path:
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise ResetRefused(f"No database file at {path}.")
    if not is_sqlite_file(path):
        raise ResetRefused(f"{path} is not an SQLite database.")
    if Path(str(path) + ".json").exists():
        raise ResetRefused(f"{path} has a backup manifest beside it: it looks like a backup. "
                           "Reset passwords only in the live database.")
    for suffix in ("-journal", "-wal"):
        if Path(str(path) + suffix).exists():
            raise ResetRefused(f"{path.name}{suffix} exists: the app may be running. Stop it first.")
    return path


def check_new_password(password: str, repeated: str, username: str) -> None:
    if password != repeated:
        raise ResetRefused("The two passwords don't match.")
    if len(password) < MIN_LENGTH:
        raise ResetRefused(f"The password must be at least {MIN_LENGTH} characters.")
    if password.strip() != password:
        raise ResetRefused("The password can't start or end with a space.")
    if password.lower() == username.lower():
        raise ResetRefused("The password can't be the same as the username.")


def reset_password(database: str, username: str, prompt=getpass.getpass) -> Path:
    path = target_database(database)
    # hide_parameters: if anything goes wrong, SQLAlchemy's error message
    # won't include the values (the new hash) that were being written.
    engine = create_engine(f"sqlite:///{path}", poolclass=NullPool, hide_parameters=True)
    enforce_foreign_keys(engine)
    try:
        try:
            check_database_revision(engine)
        except DatabaseNotReady as e:
            raise ResetRefused(f"This database isn't at the latest migration, so it's not the live "
                               f"database (or needs migrating first).\n{e}") from None
        with engine.connect() as conn:
            user_id = conn.execute(select(User.id).where(User.username == username)).scalar()
        if user_id is None:
            raise ResetRefused(f"There's no user called '{username}'. Usernames are case-sensitive.")

        password = prompt(f"New password for '{username}': ")
        repeated = prompt("Repeat the new password: ")
        check_new_password(password, repeated, username)
        new_hash = hash_password(password)

        try:
            with engine.begin() as conn:   # one transaction: all or nothing
                changed = conn.execute(
                    update(User).where(User.id == user_id).values(password_hash=new_hash)
                ).rowcount
                if changed != 1:
                    raise ResetRefused(f"Expected to change 1 user, would have changed {changed}; nothing saved.")
                stored = conn.execute(select(User.password_hash).where(User.id == user_id)).scalar()
                if not verify_password(password, stored):
                    raise ResetRefused("The new password didn't verify; nothing saved.")
        except SQLAlchemyError as e:
            raise ResetRefused(f"The database refused the change ({type(e).__name__}); nothing saved.") from None
        return path
    finally:
        engine.dispose()


def main(argv=None, prompt=getpass.getpass) -> int:
    p = argparse.ArgumentParser(description="Reset an existing user's password (app must be stopped).")
    p.add_argument("--database", required=True, help="path to the live database file, e.g. ~/FoodHeroes-data/foodheroes.db")
    p.add_argument("--username", required=True, help="an existing username, e.g. admin")
    args = p.parse_args(argv)
    try:
        path = reset_password(args.database, args.username, prompt)
    except ResetRefused as e:
        print(f"Not changed: {e}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled; nothing changed.", file=sys.stderr)
        return 1
    print(f"Password updated for '{args.username}' in {path}.")
    print("Log in with the new password. Existing login tokens stay valid until they expire,")
    print("unless the backend is restarted with a new SECRET_KEY.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
