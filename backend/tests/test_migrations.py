"""
Migration tests (P0-5 step 2). Every database is a disposable file in tmp_path;
no test touches foodheroes.db or the app's engine.
"""
import os


import shutil  # noqa: E402
import sqlite3  # noqa: E402
import textwrap  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.autogenerate import compare_metadata  # noqa: E402
from alembic.config import Config  # noqa: E402
from alembic.migration import MigrationContext  # noqa: E402
from sqlalchemy import create_engine, inspect  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.database import Base  # noqa: E402
import app.models  # noqa: E402,F401
from scripts.db_common import content_digest, connect_readonly, file_sha256  # noqa: E402
from scripts.db_preflight import preflight  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
APP_TABLES = {"users", "products", "orders", "order_allocations"}


# ---------- helpers ----------
def alembic_config(db_path, script_location=None):
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(script_location or BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    cfg.attributes["skip_logging_config"] = True
    return cfg


def schema(db_path):
    """Every table and index definition, exactly as SQLite stores it."""
    con = sqlite3.connect(db_path)
    rows = con.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' AND tbl_name != 'alembic_version' ORDER BY type, name"
    ).fetchall()
    con.close()
    return rows


def tables(db_path):
    con = sqlite3.connect(db_path)
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    return names


def revision(db_path):
    con = sqlite3.connect(db_path)
    try:
        return [r[0] for r in con.execute("SELECT version_num FROM alembic_version")]
    except sqlite3.OperationalError:
        return None
    finally:
        con.close()


def create_all_db(db_path, with_data=True):
    engine = create_engine(f"sqlite:///{db_path}", poolclass=NullPool)
    Base.metadata.create_all(engine)
    engine.dispose()
    if with_data:
        con = sqlite3.connect(db_path)
        con.execute("INSERT INTO users (username, fullname, role, email, password_hash) "
                    "VALUES ('admin', 'Administrator', 'Admin', 'a@example.com', 'x')")
        con.execute("INSERT INTO orders (order_date, customer_name, product_name, cartons, amount, amount_paid) "
                    "VALUES ('2026-02-12', 'ACSK', 'Eggs (carton)', NULL, 3000000, 1000000)")
        con.commit()
        con.close()
    return db_path


def fingerprint(path):
    st = os.stat(path)
    return file_sha256(path), st.st_size, st.st_mtime_ns


def scripts_with_extra(tmp_path, body, after="0002"):
    """A disposable copy of migrations/ with one extra revision 9999 after `after`."""
    loc = tmp_path / "migrations"
    shutil.copytree(BACKEND / "migrations", loc, ignore=shutil.ignore_patterns("__pycache__"))
    (loc / "versions" / "9999_test_only.py").write_text(textwrap.dedent('''\
        import sqlalchemy as sa
        from alembic import op
        revision = "9999"
        down_revision = "{after}"
        branch_labels = None
        depends_on = None

        def upgrade():
        ''').replace("{after}", after) + textwrap.indent(textwrap.dedent(body), "    ") + "\n\ndef downgrade():\n    pass\n")
    return loc


# ---------- the baseline ----------
def test_upgrade_creates_schema_identical_to_the_models(tmp_path):
    migrated, built = tmp_path / "migrated.db", tmp_path / "create_all.db"
    command.upgrade(alembic_config(migrated), "head")
    create_all_db(built, with_data=False)
    assert schema(migrated) == schema(built)          # same DDL text, table by table, index by index
    assert revision(migrated) == ["0002"]


def test_migrated_schema_has_no_differences_from_models(tmp_path):
    db = tmp_path / "fresh.db"
    command.upgrade(alembic_config(db), "head")
    engine = create_engine(f"sqlite:///{db}", poolclass=NullPool)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    engine.dispose()
    assert diff == []


def test_migrated_database_passes_preflight(tmp_path):
    db = tmp_path / "fresh.db"
    command.upgrade(alembic_config(db), "head")
    r = preflight(db)
    assert r.ok, r.issues
    assert r.revision == "0002" and r.known_legacy == []


def test_downgrade_and_upgrade_round_trip(tmp_path):
    db = tmp_path / "fresh.db"
    cfg = alembic_config(db)
    command.upgrade(cfg, "head")
    first = schema(db)
    command.downgrade(cfg, "base")
    assert tables(db) == {"alembic_version"} and revision(db) == []
    command.upgrade(cfg, "head")
    assert schema(db) == first


def test_naming_convention(tmp_path):
    db = tmp_path / "fresh.db"
    command.upgrade(alembic_config(db), "head")
    insp = inspect(create_engine(f"sqlite:///{db}", poolclass=NullPool))
    indexes = sorted(ix["name"] for t in APP_TABLES for ix in insp.get_indexes(t))
    assert indexes == ["ix_order_allocations_order_id", "ix_orders_customer_name", "ix_orders_id",
                       "ix_orders_order_date", "ix_products_batch_id", "ix_products_id",
                       "ix_users_id", "ix_users_username"]   # unchanged from before the convention
    fks = sorted(fk["name"] for fk in insp.get_foreign_keys("order_allocations"))
    assert fks == ["fk_order_allocations_order_id_orders", "fk_order_allocations_product_id_products"]
    assert insp.get_pk_constraint("users")["name"] == "pk_users"


# ---------- existing databases are protected ----------
def test_baseline_refuses_existing_database_and_changes_nothing(tmp_path):
    db = create_all_db(tmp_path / "existing.db")
    before = fingerprint(db)
    with pytest.raises(Exception, match="already has FoodHeroes tables"):
        command.upgrade(alembic_config(db), "head")
    assert fingerprint(db) == before                  # not even alembic_version was left behind
    assert "alembic_version" not in tables(db)


def test_refuses_to_run_without_a_database_url(tmp_path, monkeypatch):
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.attributes["skip_logging_config"] = True
    monkeypatch.delenv("DATABASE_URL")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(Exception, match="Set DATABASE_URL"):
        command.upgrade(cfg, "head")
    assert list(tmp_path.iterdir()) == []             # no database file was created


# ---------- failed migrations roll back and restore FK enforcement ----------
def run_with_connection(db, script_location, fk_on, target="head"):
    """Run an upgrade on a connection we own, so we can inspect it afterwards."""
    engine = create_engine(f"sqlite:///{db}", poolclass=NullPool)
    conn = engine.connect()
    conn.connection.driver_connection.execute(f"PRAGMA foreign_keys = {'ON' if fk_on else 'OFF'}")
    cfg = alembic_config(db, script_location)
    cfg.attributes["connection"] = conn
    error = None
    try:
        command.upgrade(cfg, target)
    except Exception as e:  # noqa: BLE001
        error = e
    fk_after = conn.connection.driver_connection.execute("PRAGMA foreign_keys").fetchone()[0]
    in_tx = conn.connection.driver_connection.in_transaction
    conn.close()
    engine.dispose()
    return error, fk_after, in_tx


@pytest.mark.parametrize("fk_on", [True, False])
def test_failed_migration_rolls_back_and_restores_fk_setting(tmp_path, fk_on):
    db = tmp_path / "db.sqlite"
    command.upgrade(alembic_config(db), "0001")
    create_data = sqlite3.connect(db)
    create_data.execute("INSERT INTO orders (order_date, customer_name, product_name, amount, amount_paid) "
                        "VALUES ('2026-02-06', '4u', 'Eggs (carton)', 2280000, 2280000)")
    create_data.commit()
    create_data.close()
    before = content_digest(connect_readonly(db))
    loc = scripts_with_extra(tmp_path, '''
        op.create_table("half_done", sa.Column("id", sa.Integer, primary_key=True))
        op.execute("UPDATE orders SET amount_paid = 0")
        raise RuntimeError("migration fails halfway")
    ''')
    error, fk_after, in_tx = run_with_connection(db, loc, fk_on)
    assert "fails halfway" in str(error)
    assert fk_after == (1 if fk_on else 0)            # enforcement setting restored
    assert in_tx is False                             # no transaction left open
    assert "half_done" not in tables(db)              # DDL rolled back
    assert content_digest(connect_readonly(db)) == before   # data rolled back
    assert revision(db) == ["0001"]                   # still at the old revision


def test_successful_migration_restores_fk_setting(tmp_path):
    db = tmp_path / "db.sqlite"
    error, fk_after, in_tx = run_with_connection(db, BACKEND / "migrations", fk_on=True)
    assert error is None and fk_after == 1 and in_tx is False
    assert revision(db) == ["0002"]


def test_foreign_keys_are_off_while_migrating(tmp_path):
    db = tmp_path / "db.sqlite"
    command.upgrade(alembic_config(db), "0001")
    loc = scripts_with_extra(tmp_path, '''
        state = op.get_bind().exec_driver_sql("PRAGMA foreign_keys").scalar()
        if state != 0:
            raise RuntimeError(f"foreign_keys was {state} during the migration")
    ''')
    error, fk_after, _ = run_with_connection(db, loc, fk_on=True)
    assert error is None and fk_after == 1
    assert revision(db) == ["9999"]


def test_migration_that_breaks_foreign_keys_is_rolled_back(tmp_path):
    """A migration that leaves an allocation pointing at a missing order must not commit."""
    db = tmp_path / "db.sqlite"
    command.upgrade(alembic_config(db), "0001")
    loc = scripts_with_extra(tmp_path, '''
        op.execute("INSERT INTO order_allocations (order_id, product_id, batch_id, quantity) "
                   "VALUES (424242, NULL, 'BATCH-X', 1)")
    ''')
    error, fk_after, _ = run_with_connection(db, loc, fk_on=True)
    assert "broken foreign key" in str(error)
    assert fk_after == 1
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM order_allocations").fetchone()[0] == 0
    con.close()
    assert revision(db) == ["0001"]


def test_refuses_a_connection_already_in_a_transaction(tmp_path):
    db = tmp_path / "db.sqlite"
    engine = create_engine(f"sqlite:///{db}", poolclass=NullPool)
    conn = engine.connect()
    conn.exec_driver_sql("SELECT 1")                  # SQLAlchemy has now begun a transaction
    cfg = alembic_config(db)
    cfg.attributes["connection"] = conn
    with pytest.raises(Exception, match="already in a transaction"):
        command.upgrade(cfg, "head")
    conn.close()
    engine.dispose()


# ---------- 0002: legacy column types (P0-5 step 3) ----------
# Table definitions exactly as older versions of the app created them (copied
# from sqlite_master of a real pre-migration database, and from commit 4ede3f5
# for the even older orders table).
LEGACY_DDL = {
    "users": "CREATE TABLE users (\n\tid INTEGER NOT NULL, \n\tusername VARCHAR(50) NOT NULL, \n\tfullname VARCHAR(100) NOT NULL, \n\trole VARCHAR(20) NOT NULL, \n\temail VARCHAR(120) NOT NULL, \n\tpassword_hash VARCHAR(128) NOT NULL, \n\tPRIMARY KEY (id)\n)",
    "products": "CREATE TABLE products (\n\tid INTEGER NOT NULL, \n\tbatch_id VARCHAR(30) NOT NULL, \n\tproduct_name VARCHAR(100) NOT NULL, \n\tprice FLOAT NOT NULL, \n\tquantity INTEGER NOT NULL, \n\tdate_added DATE NOT NULL, \n\texpiry_date DATE NOT NULL, \n\tPRIMARY KEY (id)\n)",
    "orders": "CREATE TABLE orders (\n\tid INTEGER NOT NULL, \n\torder_date DATE NOT NULL, \n\tcustomer_name VARCHAR(100) NOT NULL, \n\tproduct_name VARCHAR(100) NOT NULL, \n\tcartons FLOAT, \n\tamount FLOAT NOT NULL, \n\tamount_paid FLOAT NOT NULL, \n\tnotes VARCHAR(255), \n\tPRIMARY KEY (id)\n)",
    "order_allocations": "CREATE TABLE order_allocations (\n\tid INTEGER NOT NULL, \n\torder_id INTEGER NOT NULL, \n\tproduct_id INTEGER, \n\tbatch_id VARCHAR(30) NOT NULL, \n\tquantity FLOAT NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(order_id) REFERENCES orders (id), \n\tFOREIGN KEY(product_id) REFERENCES products (id)\n)",
}
OLDEST_ORDERS_DDL = LEGACY_DDL["orders"].replace("cartons FLOAT, ", "cartons INTEGER NOT NULL, ")
LEGACY_INDEXES = [
    "CREATE INDEX ix_order_allocations_order_id ON order_allocations (order_id)",
    "CREATE INDEX ix_orders_customer_name ON orders (customer_name)",
    "CREATE INDEX ix_orders_id ON orders (id)",
    "CREATE INDEX ix_orders_order_date ON orders (order_date)",
    "CREATE UNIQUE INDEX ix_products_batch_id ON products (batch_id)",
    "CREATE INDEX ix_products_id ON products (id)",
    "CREATE INDEX ix_users_id ON users (id)",
    "CREATE UNIQUE INDEX ix_users_username ON users (username)",
]


def legacy_db(path, oldest_orders=False):
    """A pre-migration database with whole, half and zero quantities, staff and
    admin accounts, paid / part-paid / owing orders, and allocations linking them."""
    con = sqlite3.connect(path)
    ddl = dict(LEGACY_DDL, orders=OLDEST_ORDERS_DDL) if oldest_orders else LEGACY_DDL
    for sql in [ddl["users"], ddl["products"], ddl["orders"], ddl["order_allocations"], *LEGACY_INDEXES]:
        con.execute(sql)
    con.executemany("INSERT INTO users VALUES (?,?,?,?,?,?)", [
        (1, "admin", "Administrator", "Admin", "admin@example.com", "$2b$12$adminhash"),
        (2, "ada", "Ada Staff", "User", "ada@example.com", "$2b$12$staffhash")])
    con.executemany("INSERT INTO products VALUES (?,?,?,?,?,?,?)", [
        (1, "BATCH-20261023-001", "Eggs (crate)", 4500.0, 20, "2026-10-23", "2026-11-22"),     # whole number
        (2, "BATCH-20261024-001", "Eggs (crate)", 4500.0, 37.5, "2026-10-24", "2026-11-23"),   # half carton
        (3, "BATCH-20261001-001", "Eggs (tray)", 1500.0, 0, "2026-10-01", "2026-10-31")])      # emptied
    cartons = [38, 2, 5] if oldest_orders else [38, None, 2.5]
    con.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?)", [
        (1, "2026-02-06", "4u", "Eggs (carton)", cartons[0], 2280000.0, 2280000.0, None),
        (2, "2026-02-12", "Unknown", "Eggs (carton)", cartons[1], 53000.0, 0.0, "Name unclear"),
        (3, "2026-10-25", "Pharmcare", "Eggs (crate)", cartons[2], 11250.0, 5000.0, None)])
    con.executemany("INSERT INTO order_allocations VALUES (?,?,?,?,?)", [
        (1, 3, 1, "BATCH-20261023-001", 2.0),
        (2, 3, 2, "BATCH-20261024-001", 0.5)])
    con.commit()
    con.close()
    return path


def snapshot(db):
    """Every row of every app table, by id. Compared with ==, so 20 equals 20.0."""
    con = sqlite3.connect(db)
    out = {t: con.execute(f"SELECT * FROM {t} ORDER BY id").fetchall() for t in sorted(APP_TABLES)}
    con.close()
    return out


def definitions(db):
    con = sqlite3.connect(db)
    out = {n: s for n, s in con.execute("SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL AND tbl_name != 'alembic_version'")}
    con.close()
    return out


def table_digest(db, table):
    """Exact stored values and storage types of one table (detects ANY change, even 20 -> 20.0)."""
    con = sqlite3.connect(db)
    cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    typed = ", ".join(f"{c}, typeof({c})" for c in cols)
    rows = con.execute(f"SELECT {typed} FROM {table} ORDER BY id").fetchall()
    con.close()
    return rows


def allocation_links(db):
    """Which order took how much from which batch: the relationships that must survive."""
    con = sqlite3.connect(db)
    rows = con.execute(
        "SELECT a.id, o.id, o.customer_name, p.id, p.batch_id, a.quantity FROM order_allocations a "
        "JOIN orders o ON o.id = a.order_id JOIN products p ON p.id = a.product_id ORDER BY a.id").fetchall()
    con.close()
    return rows


def stamp_and_upgrade(db):
    command.stamp(alembic_config(db), "0001")
    command.upgrade(alembic_config(db), "head")


def unquote(sql):
    """A table rebuilt by rename is stored as CREATE TABLE "name" (...): same table."""
    import re
    return re.sub(r'^CREATE TABLE "(\w+)"', r"CREATE TABLE \1", sql)


def assert_matches_models(db):
    r = preflight(db)
    assert r.ok, r.issues
    assert r.known_legacy == [] and r.orphans == [] and r.revision == "0002"
    engine = create_engine(f"sqlite:///{db}", poolclass=NullPool)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    engine.dispose()
    assert diff == []
    con = sqlite3.connect(db)
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    con.close()


def test_legacy_database_is_ready_to_stamp(tmp_path):
    db = legacy_db(tmp_path / "legacy.db")
    r = preflight(db)
    assert r.ok and r.revision is None
    assert [(d["table"], d["column"]) for d in r.known_legacy] == [("products", "quantity")]


def test_0002_fixes_quantity_and_preserves_every_record(tmp_path):
    db = legacy_db(tmp_path / "legacy.db")
    rows, defs, links = snapshot(db), definitions(db), allocation_links(db)
    untouched = {t: table_digest(db, t) for t in ("users", "orders", "order_allocations")}
    stamp_and_upgrade(db)

    assert revision(db) == ["0002"]
    assert snapshot(db) == rows                                   # every record, every value
    assert allocation_links(db) == links                         # every relationship
    con = sqlite3.connect(db)
    assert con.execute("SELECT id, quantity, typeof(quantity) FROM products ORDER BY id").fetchall() == [
        (1, 20.0, "real"), (2, 37.5, "real"), (3, 0.0, "real")]
    con.close()
    for t, digest in untouched.items():
        assert table_digest(db, t) == digest                     # not even the storage type changed
    changed = sorted(n for n, s in definitions(db).items() if s != defs.get(n))
    assert changed == ["products"]                               # only the table that needed it
    assert_matches_models(db)


def test_0002_also_fixes_the_oldest_orders_table(tmp_path):
    db = legacy_db(tmp_path / "oldest.db", oldest_orders=True)
    rows, defs, links = snapshot(db), definitions(db), allocation_links(db)
    users, allocations = table_digest(db, "users"), table_digest(db, "order_allocations")
    stamp_and_upgrade(db)

    assert snapshot(db) == rows and allocation_links(db) == links
    assert table_digest(db, "users") == users and table_digest(db, "order_allocations") == allocations
    assert sorted(n for n, s in definitions(db).items() if s != defs.get(n)) == ["orders", "products"]
    assert_matches_models(db)
    con = sqlite3.connect(db)                                    # blank cartons are now allowed
    con.execute("INSERT INTO orders (order_date, customer_name, product_name, cartons, amount, amount_paid) "
                "VALUES ('2026-11-05', 'Test', 'Eggs (crate)', NULL, 1, 0)")
    con.rollback()
    con.close()


def test_legacy_result_matches_a_fresh_install(tmp_path):
    legacy, fresh = legacy_db(tmp_path / "legacy.db"), tmp_path / "fresh.db"
    stamp_and_upgrade(legacy)
    command.upgrade(alembic_config(fresh), "head")
    got, want = definitions(legacy), definitions(fresh)
    assert set(got) == set(want)                                  # same tables and indexes
    assert unquote(got["products"]) == want["products"]           # rebuilt table: identical definition
    for name in [n for n in want if n.startswith("ix_")]:
        assert got[name] == want[name]                            # all 8 indexes identical
    for name in ("users", "orders", "order_allocations"):
        # Untouched tables keep exactly their old definition: the same columns
        # and rules as a fresh install, but without constraint names.
        assert got[name] == LEGACY_DDL[name]
        assert want[name] != LEGACY_DDL[name] and "CONSTRAINT pk_" in want[name]


def test_0002_does_nothing_on_a_fresh_database(tmp_path):
    db = tmp_path / "fresh.db"
    command.upgrade(alembic_config(db), "0001")
    run_sql = sqlite3.connect(db)
    run_sql.execute("INSERT INTO products (batch_id, product_name, price, quantity, date_added, expiry_date) "
                    "VALUES ('BATCH-20261105-001', 'Eggs (crate)', 4500, 37.5, '2026-11-05', '2026-12-05')")
    run_sql.commit()
    run_sql.close()
    defs, data = definitions(db), {t: table_digest(db, t) for t in APP_TABLES}
    command.upgrade(alembic_config(db), "head")
    assert revision(db) == ["0002"]
    assert definitions(db) == defs                               # no table rebuilt
    assert {t: table_digest(db, t) for t in APP_TABLES} == data


def test_0002_only_rebuilds_once(tmp_path):
    db = legacy_db(tmp_path / "legacy.db")
    stamp_and_upgrade(db)
    defs, data = definitions(db), {t: table_digest(db, t) for t in APP_TABLES}
    command.downgrade(alembic_config(db), "0001")                # 0002's downgrade changes nothing
    assert definitions(db) == defs and revision(db) == ["0001"]
    command.upgrade(alembic_config(db), "head")                  # nothing left to fix
    assert definitions(db) == defs
    assert {t: table_digest(db, t) for t in APP_TABLES} == data


@pytest.mark.parametrize("fk_on", [True, False])
def test_failure_after_0002_rolls_back_the_rebuild(tmp_path, fk_on):
    """0002 rebuilds products, then a later step fails: the rebuild must be undone too."""
    db = legacy_db(tmp_path / "legacy.db")
    command.stamp(alembic_config(db), "0001")
    defs = definitions(db)
    exact = {t: table_digest(db, t) for t in APP_TABLES}
    loc = scripts_with_extra(tmp_path, '''
        raise RuntimeError("a later migration fails")
    ''', after="0002")
    error, fk_after, in_tx = run_with_connection(db, loc, fk_on)
    assert "later migration fails" in str(error)
    assert fk_after == (1 if fk_on else 0) and in_tx is False   # enforcement setting restored
    assert definitions(db) == defs                               # products still the legacy INTEGER table
    assert {t: table_digest(db, t) for t in APP_TABLES} == exact # 20 is still stored as integer 20
    assert revision(db) == ["0001"]
    assert preflight(db).ok                                      # still a valid, stampable-state database


def test_0002_refuses_to_leave_broken_references(tmp_path):
    """If the rebuild could ever break a reference, foreign_key_check rolls everything back."""
    db = legacy_db(tmp_path / "legacy.db")
    command.stamp(alembic_config(db), "0001")
    exact = {t: table_digest(db, t) for t in APP_TABLES}
    loc = scripts_with_extra(tmp_path, '''
        op.execute("DELETE FROM products WHERE id = 1")   # allocation 1 now points at nothing
    ''', after="0002")
    error, fk_after, _ = run_with_connection(db, loc, fk_on=True)
    assert "broken foreign key" in str(error) and fk_after == 1
    assert {t: table_digest(db, t) for t in APP_TABLES} == exact
    assert revision(db) == ["0001"]
