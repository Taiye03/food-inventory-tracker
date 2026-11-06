"""
Check that the database is at the latest migration before using it (P0-5 step 4).

The app no longer creates or alters tables itself. Schema changes are made
only by `alembic upgrade head`, run deliberately after a backup. At startup
(and before the ledger import) this module checks the database and refuses to
continue, with instructions, if it is:

  missing      no database file, or a database with no tables
  unversioned  FoodHeroes tables exist but Alembic doesn't manage them yet
  outdated     managed by Alembic but behind the code's latest migration
  unknown      at a revision this code doesn't have (e.g. the code is older
               than the database)

The check only reads. In particular it never creates a missing SQLite file,
which a normal connection would do.
"""
from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.engine import Engine, make_url

BACKEND = Path(__file__).resolve().parents[1]
APP_TABLES = {"users", "products", "orders", "order_allocations"}
GUIDE = "See docs/DATABASE_RECOVERY.md."


class DatabaseNotReady(RuntimeError):
    """The database can't be used by this version of the code. The message says what to do."""

    def __init__(self, state: str, message: str):
        super().__init__(message)
        self.state = state


def _config() -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    return cfg


def latest_revision() -> str:
    heads = ScriptDirectory.from_config(_config()).get_heads()
    if len(heads) != 1:
        raise DatabaseNotReady("code", f"The migrations have {len(heads)} heads ({heads}); expected exactly one.")
    return heads[0]


def _describe(engine: Engine) -> str:
    return make_url(engine.url).render_as_string(hide_password=True)


def _sqlite_file(engine: Engine) -> Path | None:
    url = make_url(engine.url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return None
    return Path(url.database).resolve()


def check_database_revision(engine: Engine) -> str:
    """Return the revision if the database is ready; otherwise raise DatabaseNotReady."""
    head = latest_revision()
    sqlite_file = _sqlite_file(engine)
    # Show the full path, so a relative DATABASE_URL can't send alembic to a different file.
    where = str(sqlite_file) if sqlite_file else _describe(engine)
    url_hint = f"DATABASE_URL=sqlite:///{sqlite_file}" if sqlite_file else f"DATABASE_URL={_describe(engine)}"
    # The SQLite tools take the file as an argument; name it, so every step works on the same file.
    db_arg = f" {sqlite_file}" if sqlite_file else ""
    backup_arg = f" --source {sqlite_file}" if sqlite_file else ""

    if sqlite_file is not None and not sqlite_file.exists():
        raise DatabaseNotReady("missing", (
            f"No database at {sqlite_file}.\n"
            f"  - New installation: create it with\n"
            f"        {url_hint} alembic upgrade head\n"
            f"  - Existing database somewhere else: set DATABASE_URL to its path.\n"
            f"Nothing was created. {GUIDE}"
        ))

    with engine.connect() as conn:
        tables = set(inspect(conn).get_table_names())
        current = MigrationContext.configure(conn).get_current_heads() if "alembic_version" in tables else ()

    if not current and not (tables & APP_TABLES):
        raise DatabaseNotReady("missing", (
            f"The database at {where} has no FoodHeroes tables.\n"
            f"  - New installation: create them with\n"
            f"        {url_hint} alembic upgrade head\n"
            f"{GUIDE}"
        ))
    if not current:
        raise DatabaseNotReady("unversioned", (
            f"The database at {where} has FoodHeroes tables but isn't under migration control yet.\n"
            f"Do NOT run 'alembic upgrade head' on it directly. Bring it under migrations safely:\n"
            f"  1. stop the app\n"
            f"  2. python -m scripts.db_preflight{db_arg}      (must report no issues)\n"
            f"  3. python -m scripts.backup_db backup{backup_arg}  (then copy the backup off this computer)\n"
            f"  4. {url_hint} alembic stamp 0001\n"
            f"  5. {url_hint} alembic upgrade head\n"
            f"  6. python -m scripts.db_preflight{db_arg}      (must report revision {head}, no issues)\n"
            f"{GUIDE}"
        ))
    if len(current) > 1:
        raise DatabaseNotReady("unknown", f"The database at {where} has several revisions recorded {current}. {GUIDE}")

    (rev,) = current
    if rev == head:
        return rev
    known = {s.revision for s in ScriptDirectory.from_config(_config()).walk_revisions()}
    if rev in known:
        raise DatabaseNotReady("outdated", (
            f"The database at {where} is at migration {rev}; this code needs {head}.\n"
            f"Update it (after a backup):\n"
            f"  1. stop the app\n"
            f"  2. python -m scripts.backup_db backup{backup_arg}\n"
            f"  3. {url_hint} alembic upgrade head\n"
            f"  4. python -m scripts.db_preflight{db_arg}      (must report revision {head}, no issues)\n"
            f"{GUIDE}"
        ))
    raise DatabaseNotReady("unknown", (
        f"The database at {where} is at migration {rev}, which this code doesn't know about "
        f"(latest known: {head}). It was probably migrated by newer code: update the code, "
        f"don't change the database. {GUIDE}"
    ))
