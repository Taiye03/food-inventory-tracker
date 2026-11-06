"""
Tests for scripts/backup_db.py and scripts/db_preflight.py (P0-5 step 1).

Every database here is a disposable file in pytest's tmp_path. Nothing touches
foodheroes.db or the app's engine.
"""
import os


import json  # noqa: E402
import sqlite3  # noqa: E402
from datetime import date  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402

from app.database import Base  # noqa: E402
import app.models  # noqa: E402,F401
from scripts.backup_db import backup, main as backup_main, manifest_path, restore, verify_backup  # noqa: E402
from scripts.db_common import DatabaseToolError, connect_readonly, content_digest, file_sha256  # noqa: E402
from scripts.db_preflight import main as preflight_main, preflight  # noqa: E402


# ---------- disposable databases ----------
def make_db(path):
    """A database exactly as today's models create it, with a little data."""
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    con = sqlite3.connect(path)
    con.execute("INSERT INTO users (username, fullname, role, email, password_hash) "
                "VALUES ('admin', 'Administrator', 'Admin', 'a@example.com', 'x')")
    con.execute("INSERT INTO products (batch_id, product_name, price, quantity, date_added, expiry_date) "
                "VALUES ('BATCH-20261020-001', 'Eggs (crate)', 4500, 37.5, '2026-10-20', '2026-11-19')")
    con.execute("INSERT INTO orders (order_date, customer_name, product_name, cartons, amount, amount_paid) "
                "VALUES ('2026-10-21', 'Pharmcare', 'Eggs (crate)', 2.5, 120000, 0)")
    con.execute("INSERT INTO order_allocations (order_id, product_id, batch_id, quantity) "
                "VALUES (1, 1, 'BATCH-20261020-001', 2.5)")
    con.commit()
    con.close()
    return path


def run_sql(path, *statements):
    con = sqlite3.connect(path)
    for s in statements:
        con.execute(s)
    con.commit()
    con.close()


def rebuild_table(path, table, create_sql):
    """Recreate a table with different DDL, keeping its rows (simulates an older database).

    legacy_alter_table=ON stops SQLite from rewriting other tables' foreign keys
    to point at the renamed table (see test_preflight_catches_foreign_key_retargeted_by_rename).
    """
    run_sql(path, "PRAGMA legacy_alter_table = ON", f"ALTER TABLE {table} RENAME TO {table}_tmp", create_sql,
            f"INSERT INTO {table} SELECT * FROM {table}_tmp", f"DROP TABLE {table}_tmp")


def fingerprint(path):
    """Everything that would change if a tool wrote to the file."""
    st = os.stat(path)
    return file_sha256(path), st.st_size, st.st_mtime_ns


@pytest.fixture()
def db(tmp_path):
    return make_db(tmp_path / "foodheroes.db")


def kinds(report):
    return sorted(i["kind"] for i in report.issues)


# ---------- preflight ----------
def test_preflight_clean_database(db):
    r = preflight(db)
    assert r.ok, r.issues
    assert r.integrity == "ok"
    assert r.revision is None
    assert r.row_counts == {"order_allocations": 1, "orders": 1, "products": 1, "users": 1}
    assert r.known_legacy == [] and r.orphans == []


def test_preflight_never_writes(db):
    before = fingerprint(db)
    preflight(db)
    preflight_main([str(db), "--json"])
    assert fingerprint(db) == before
    assert not os.path.exists(str(db) + "-journal")


def test_preflight_reports_known_legacy_quantity_type(db):
    rebuild_table(db, "products", """CREATE TABLE products (
        id INTEGER NOT NULL, batch_id VARCHAR(30) NOT NULL, product_name VARCHAR(100) NOT NULL,
        price FLOAT NOT NULL, quantity INTEGER NOT NULL, date_added DATE NOT NULL,
        expiry_date DATE NOT NULL, PRIMARY KEY (id))""")
    run_sql(db, "CREATE INDEX ix_products_id ON products (id)",
            "CREATE UNIQUE INDEX ix_products_batch_id ON products (batch_id)")
    r = preflight(db)
    assert r.ok, r.issues  # known legacy differences are not issues
    assert [(d["table"], d["column"], d["found"]) for d in r.known_legacy] == [("products", "quantity", "INTEGER")]
    con = sqlite3.connect(db)
    assert con.execute("SELECT quantity FROM products").fetchone()[0] == 37.5  # half carton survived
    con.close()


def test_preflight_flags_unexpected_drift(db):
    rebuild_table(db, "users", """CREATE TABLE users (
        id INTEGER NOT NULL, username VARCHAR(50) NOT NULL, fullname VARCHAR(100) NOT NULL,
        role VARCHAR(20) NOT NULL, email TEXT, password_hash VARCHAR(128) NOT NULL, PRIMARY KEY (id))""")
    run_sql(db, "CREATE INDEX ix_users_id ON users (id)", "CREATE UNIQUE INDEX ix_users_username ON users (username)",
            "ALTER TABLE users ADD COLUMN nickname TEXT")
    r = preflight(db)
    assert kinds(r) == ["column_nullable", "column_type", "unknown_column"]


def test_preflight_missing_and_unknown_tables(db):
    run_sql(db, "DROP TABLE order_allocations",
            "CREATE TABLE orders_old (id INTEGER)")
    r = preflight(db)
    assert kinds(r) == ["missing_table", "unknown_table"]
    assert "interrupted upgrade_orders_table" in next(i for i in r.issues if i["kind"] == "unknown_table")["message"]


def test_preflight_allows_alembic_version_and_reports_revision(db):
    run_sql(db, "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)",
            "INSERT INTO alembic_version VALUES ('0001')")
    r = preflight(db)
    assert r.ok and r.revision == "0001"


def test_preflight_missing_and_unknown_indexes(db):
    run_sql(db, "DROP INDEX ix_orders_order_date", "CREATE INDEX ix_orders_notes ON orders (notes)")
    r = preflight(db)
    assert kinds(r) == ["missing_index", "unknown_index"]


def test_preflight_missing_unique_index(db):
    run_sql(db, "DROP INDEX ix_users_username", "CREATE INDEX ix_users_username ON users (username)")
    assert kinds(preflight(db)) == ["index_differs"]


def test_preflight_missing_foreign_key_declaration(db):
    rebuild_table(db, "order_allocations", """CREATE TABLE order_allocations (
        id INTEGER NOT NULL, order_id INTEGER NOT NULL, product_id INTEGER,
        batch_id VARCHAR(30) NOT NULL, quantity FLOAT NOT NULL,
        PRIMARY KEY (id), FOREIGN KEY(order_id) REFERENCES orders (id))""")
    run_sql(db, "CREATE INDEX ix_order_allocations_order_id ON order_allocations (order_id)")
    assert kinds(preflight(db)) == ["missing_foreign_key"]


def test_preflight_catches_foreign_key_retargeted_by_rename(db):
    """Modern SQLite rewrites child foreign keys when a parent table is renamed.
    Rebuilding a table with rename-copy-drop then leaves the FK pointing at a
    table that no longer exists: exactly what the preflight must catch."""
    run_sql(db, "ALTER TABLE products RENAME TO products_tmp",
            "CREATE TABLE products AS SELECT * FROM products_tmp", "DROP TABLE products_tmp")
    r = preflight(db)
    msgs = " | ".join(i["message"] for i in r.issues)
    assert "order_allocations.product_id -> products.id is not declared" in msgs
    assert "order_allocations.product_id -> products_tmp.id is not in the models" in msgs


def test_preflight_lists_each_orphan_without_changing_it(db):
    """A sold batch deleted by pre-P0-3 code, and an allocation for a deleted order."""
    run_sql(db, "DELETE FROM products WHERE id = 1",
            "INSERT INTO order_allocations (order_id, product_id, batch_id, quantity) VALUES (99, NULL, 'X', 1)")
    before = fingerprint(db)
    r = preflight(db)
    assert kinds(r) == ["orphaned_rows"]
    found = sorted((o["table"], o["column"], o["missing"]) for o in r.orphans)
    assert found == [("order_allocations", "order_id", "orders.id = 99"),
                     ("order_allocations", "product_id", "products.id = 1")]
    batch_orphan = next(o for o in r.orphans if o["column"] == "product_id")
    assert batch_orphan["row"]["batch_id"] == "BATCH-20261020-001"  # enough detail to investigate
    assert fingerprint(db) == before


def test_preflight_cli_exit_codes(db, tmp_path, capsys):
    assert preflight_main([str(db)]) == 0
    run_sql(db, "DROP TABLE order_allocations")
    assert preflight_main([str(db)]) == 1
    assert preflight_main([str(tmp_path / "nope.db")]) == 2
    out = capsys.readouterr()
    assert "RESULT: issues found" in out.out and "No database file" in out.err


def test_tools_refuse_non_sqlite_and_hot_journal(db, tmp_path):
    fake = tmp_path / "notes.db"
    fake.write_text("not a database")
    with pytest.raises(DatabaseToolError, match="not an SQLite"):
        preflight(fake)
    (tmp_path / "foodheroes.db-journal").write_bytes(b"x")
    with pytest.raises(DatabaseToolError, match="journal"):
        preflight(db)
    with pytest.raises(DatabaseToolError, match="journal"):
        backup(db, tmp_path / "backups")


def test_readonly_connection_cannot_write(db):
    con = connect_readonly(db)
    with pytest.raises(sqlite3.OperationalError):
        con.execute("DELETE FROM orders")
    con.close()


# ---------- backup ----------
def test_backup_is_verified_and_leaves_source_untouched(db, tmp_path):
    before = fingerprint(db)
    res = backup(db, tmp_path / "backups")
    assert fingerprint(db) == before
    assert res.integrity == "ok"
    assert res.row_counts == {"order_allocations": 1, "orders": 1, "products": 1, "users": 1}
    m = json.loads(manifest_path(tmp_path / "backups" / os.path.basename(res.backup)).read_text())
    assert m["sha256"] == file_sha256(res.backup) and m["source"] == str(db.resolve())
    src = connect_readonly(db)
    assert m["content_digest"] == content_digest(src)
    src.close()
    assert verify_backup(res.backup)["row_counts"] == res.row_counts


def test_backups_never_overwrite(db, tmp_path):
    a = backup(db, tmp_path / "backups")
    b = backup(db, tmp_path / "backups")  # same second: gets a new name
    assert a.backup != b.backup
    assert file_sha256(a.backup) == json.loads(open(a.manifest).read())["sha256"]


def test_verify_detects_a_changed_backup(db, tmp_path):
    res = backup(db, tmp_path / "backups")
    run_sql(res.backup, "UPDATE orders SET amount_paid = 120000")
    with pytest.raises(DatabaseToolError, match="checksum.*row contents"):
        verify_backup(res.backup)


def test_backup_cli(db, tmp_path, capsys):
    assert backup_main(["backup", "--source", str(db), "--dest-dir", str(tmp_path / "b")]) == 0
    assert "Backup OK" in capsys.readouterr().out
    assert backup_main(["backup", "--source", str(tmp_path / "missing.db"), "--dest-dir", str(tmp_path / "b")]) == 1


# ---------- restore drill ----------
def test_restore_to_scratch_copy_brings_back_exact_data(db, tmp_path):
    res = backup(db, tmp_path / "backups")
    (tmp_path / "drill").mkdir()
    restored = tmp_path / "drill" / "restored.db"
    out = restore(res.backup, restored)
    assert out["moved_aside"] is None
    con = connect_readonly(restored)
    assert content_digest(con) == res.content_digest
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert con.execute("SELECT quantity, date_added FROM products").fetchone() == (37.5, "2026-10-20")
    con.close()
    assert preflight(restored).ok


def test_restore_after_damage_recovers_and_keeps_damaged_file(db, tmp_path):
    """The real recovery path: something went wrong, restore over the live file."""
    res = backup(db, tmp_path / "backups")
    run_sql(db, "DELETE FROM orders", "DROP TABLE order_allocations")   # the "bad migration"
    with pytest.raises(DatabaseToolError, match="already exists"):
        restore(res.backup, db)                                         # needs an explicit --replace
    out = restore(res.backup, db, replace=True)
    con = connect_readonly(db)
    assert content_digest(con) == res.content_digest                    # every row back
    con.close()
    kept = out["moved_aside"]
    assert kept and os.path.exists(kept)                                # damaged file kept, not deleted
    assert "order_allocations" not in [r[0] for r in sqlite3.connect(kept).execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]


def test_restore_refuses_unverified_backup(db, tmp_path):
    res = backup(db, tmp_path / "backups")
    os.remove(res.manifest)
    with pytest.raises(DatabaseToolError, match="No manifest"):
        restore(res.backup, tmp_path / "x.db")
    assert not (tmp_path / "x.db").exists()


def test_backup_handles_dates_and_fractions(db, tmp_path):
    res = backup(db, tmp_path / "backups")
    con = connect_readonly(res.backup)
    assert con.execute("SELECT order_date, cartons FROM orders").fetchone() == (str(date(2026, 10, 21)), 2.5)
    con.close()
