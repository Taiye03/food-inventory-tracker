"""
Create the FIRST administrator account in a FoodHeroes database (P0-7).

The app no longer creates an admin automatically and has no default password:
it refuses to start until an administrator exists. Run this once, from
backend/, with the app stopped, after `alembic upgrade head`:

    python -m scripts.create_admin --database ~/FoodHeroes-data/foodheroes.db \\
        --username owner --fullname "Business Owner" --email owner@example.com

You're asked for the password twice. It isn't shown as you type, and it never
appears on screen, in your shell history, in logs or in error messages.

What it does: adds exactly one new user with role Admin. It never changes,
replaces or deletes an existing account, and touches no other table.

What it refuses (and changes nothing):
  - no --database / --username / --fullname / --email (there is no default database)
  - a path that doesn't exist or isn't an SQLite database (never creates one)
  - a backup: any file with a backup manifest (<file>.json) beside it
  - a database that isn't at the latest migration (all pre-migration databases and backups)
  - a journal file beside the database (the app may be running)
  - a database that already has an administrator: to get back into an account,
    use scripts/reset_password.py instead
  - a username that already exists, or an invalid username, name or email
  - passwords that don't match, are shorter than 12 characters, longer than
    bcrypt's 72-byte limit, start or end with a space, or equal the username
"""
import argparse
import getpass
import sys
from pathlib import Path

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import func, insert, select
from sqlalchemy.exc import SQLAlchemyError

from app.auth import hash_password, verify_password
from app.models import User
from app.passwords import PasswordRejected, check_new_password
from scripts.live_database import NotLiveDatabase, live_database_path, open_live_engine, require_latest_migration

_EMAIL = TypeAdapter(EmailStr)


class CreateRefused(Exception):
    """Something isn't safe or isn't right; nothing has been changed."""


def check_details(username: str, fullname: str, email: str) -> None:
    """Same limits as the API's user form (app/schemas.py UserCreate)."""
    for label, value, limit in (("Username", username, 50), ("Full name", fullname, 100)):
        if not value.strip():
            raise CreateRefused(f"{label} can't be empty.")
        if value != value.strip():
            raise CreateRefused(f"{label} can't start or end with a space.")
        if len(value) > limit:
            raise CreateRefused(f"{label} can be at most {limit} characters.")
    if any(ch.isspace() for ch in username):
        raise CreateRefused("Username can't contain spaces.")
    if len(email) > 120:
        raise CreateRefused("Email can be at most 120 characters.")
    try:
        _EMAIL.validate_python(email)
    except ValidationError:
        raise CreateRefused(f"{email!r} isn't a valid email address.") from None


def create_admin(database: str, username: str, fullname: str, email: str, prompt=getpass.getpass) -> Path:
    check_details(username, fullname, email)
    try:
        path = live_database_path(database, action="Create accounts")
    except NotLiveDatabase as e:
        raise CreateRefused(str(e)) from None
    engine = open_live_engine(path)   # hide_parameters: errors never show the hash
    admins = select(func.count()).select_from(User).where(User.role == "Admin")
    same_name = select(func.count()).select_from(User).where(User.username == username)
    try:
        try:
            require_latest_migration(engine)
        except NotLiveDatabase as e:
            raise CreateRefused(str(e)) from None
        with engine.connect() as conn:
            if conn.execute(admins).scalar():
                raise CreateRefused("This database already has an administrator, so nothing was created. "
                                    "To get back into an existing account, use "
                                    "`python -m scripts.reset_password` instead.")
            if conn.execute(same_name).scalar():
                raise CreateRefused(f"A user called '{username}' already exists. Existing accounts are never "
                                    "changed by this tool.")

        password = prompt(f"Password for the new administrator '{username}': ")
        repeated = prompt("Repeat the password: ")
        try:
            check_new_password(password, repeated, username)
        except PasswordRejected as e:
            raise CreateRefused(str(e)) from None
        new_hash = hash_password(password)

        try:
            with engine.begin() as conn:   # one transaction: all or nothing
                conn.execute(insert(User).values(username=username, fullname=fullname, role="Admin",
                                                 email=email, password_hash=new_hash))
                # Re-check inside the same transaction: if another run created an
                # admin (or this username) in the meantime, undo this one.
                if conn.execute(admins).scalar() != 1 or conn.execute(same_name).scalar() != 1:
                    raise CreateRefused("Another administrator or a user with this name appeared while "
                                        "this was running; nothing saved.")
                stored = conn.execute(select(User.password_hash).where(User.username == username)).scalar()
                if not verify_password(password, stored):
                    raise CreateRefused("The new password didn't verify; nothing saved.")
        except SQLAlchemyError as e:
            raise CreateRefused(f"The database refused the change ({type(e).__name__}); nothing saved.") from None
        return path
    finally:
        engine.dispose()


def main(argv=None, prompt=getpass.getpass) -> int:
    p = argparse.ArgumentParser(description="Create the first administrator account (app must be stopped).")
    p.add_argument("--database", required=True, help="path to the live database file, e.g. ~/FoodHeroes-data/foodheroes.db")
    p.add_argument("--username", required=True, help="login name for the new administrator")
    p.add_argument("--fullname", required=True, help="the person's full name")
    p.add_argument("--email", required=True, help="the person's email address")
    args = p.parse_args(argv)
    try:
        path = create_admin(args.database, args.username, args.fullname, args.email, prompt)
    except CreateRefused as e:
        print(f"Not created: {e}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled; nothing created.", file=sys.stderr)
        return 1
    print(f"Administrator '{args.username}' created in {path}.")
    print("Start the backend and log in with this account.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
