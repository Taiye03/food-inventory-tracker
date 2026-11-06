"""
Foreign-key enforcement is ON by default for the app's SQLite connections
(P0-5 step 6). These tests use the app's own engine and sessions, without the
foreign_keys_enforced() test helper, so they prove the app does it by itself.
All on the throwaway test database or disposable files in tmp_path.
"""
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from app.database import SessionLocal, engine, enforce_foreign_keys
from app.main import app
from app.models import Order, OrderAllocation, Product
from tests.db_helpers import TEST_ADMIN_PASSWORD, clear_test_database, reset_test_database

TODAY = date.today()
BACKEND = __import__("pathlib").Path(__file__).resolve().parents[1]


@pytest.fixture()
def db():
    """The test database at the latest migration, with one batch, one sale and its allocation."""
    reset_test_database()
    engine.dispose()                                     # start from fresh connections
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO products (id, batch_id, product_name, price, quantity, date_added, expiry_date) "
                          "VALUES (1, 'BATCH-20261105-001', 'Eggs (crate)', 4500, 6, :d, :e)"),
                     {"d": str(TODAY), "e": str(TODAY + timedelta(days=10))})
        conn.execute(text("INSERT INTO orders (id, order_date, customer_name, product_name, cartons, amount, amount_paid) "
                          "VALUES (1, :d, 'Mama Ade', 'Eggs (crate)', 4, 18000, 0)"), {"d": str(TODAY)})
        conn.execute(text("INSERT INTO order_allocations (id, order_id, product_id, batch_id, quantity) "
                          "VALUES (1, 1, 1, 'BATCH-20261105-001', 4)"))
    yield
    clear_test_database()


def rows(sql):
    with engine.connect() as conn:
        return conn.execute(text(sql)).fetchall()


# ---------- on by default ----------
def test_every_app_connection_enforces_foreign_keys(db):
    engine.dispose()
    conns = [engine.connect() for _ in range(3)]         # several pooled connections, not just the first
    try:
        assert [c.execute(text("PRAGMA foreign_keys")).scalar() for c in conns] == [1, 1, 1]
    finally:
        for c in conns:
            c.close()
    session = SessionLocal()
    assert session.execute(text("PRAGMA foreign_keys")).scalar() == 1   # ORM sessions too
    session.close()


def test_only_sqlite_engines_get_the_pragma():
    """PostgreSQL enforces foreign keys natively; nothing is registered for it."""
    assert enforce_foreign_keys(SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))) is False
    assert enforce_foreign_keys(SimpleNamespace(dialect=SimpleNamespace(name="mysql"))) is False


# ---------- rejected by the database itself ----------
def test_database_rejects_deleting_a_batch_used_by_an_allocation(db):
    """The P0-3 endpoint check gives the friendly 409; this proves the database
    itself also refuses, even for code that bypasses the endpoint."""
    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM products WHERE id = 1"))
    session = SessionLocal()
    session.delete(session.get(Product, 1))
    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        session.commit()
    session.close()
    assert rows("SELECT id, quantity FROM products") == [(1, 6.0)]           # batch still there
    assert rows("SELECT id, product_id FROM order_allocations") == [(1, 1)]  # link intact


def test_database_rejects_deleting_an_order_that_has_allocations(db):
    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM orders WHERE id = 1"))
    assert rows("SELECT COUNT(*) FROM orders") == [(1,)]


@pytest.mark.parametrize("order_id, product_id", [(999, 1), (1, 999), (999, 999)])
def test_database_rejects_allocations_pointing_at_nothing(db, order_id, product_id):
    session = SessionLocal()
    session.add(OrderAllocation(order_id=order_id, product_id=product_id, batch_id="BATCH-X", quantity=1))
    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        session.commit()
    session.close()
    assert rows("SELECT COUNT(*) FROM order_allocations") == [(1,)]


def test_database_rejects_repointing_an_allocation_at_nothing(db):
    for sql in ("UPDATE order_allocations SET order_id = 999 WHERE id = 1",
                "UPDATE order_allocations SET product_id = 999 WHERE id = 1"):
        with pytest.raises(IntegrityError, match="FOREIGN KEY"):
            with engine.begin() as conn:
                conn.execute(text(sql))
    assert rows("SELECT order_id, product_id FROM order_allocations") == [(1, 1)]


def test_allocation_without_a_batch_is_still_allowed(db):
    """product_id is nullable by design (models.py); enforcement doesn't change that."""
    session = SessionLocal()
    session.add(OrderAllocation(order_id=1, product_id=None, batch_id="BATCH-OLD", quantity=1))
    session.commit()
    session.close()
    assert rows("SELECT COUNT(*) FROM order_allocations WHERE product_id IS NULL") == [(1,)]
    assert rows("PRAGMA foreign_key_check") == []


# ---------- normal business flow still works, with enforcement on ----------
def test_full_sale_lifecycle_with_enforcement_on():
    reset_test_database()
    engine.dispose()
    with TestClient(app) as c:
        assert rows("PRAGMA foreign_keys") == [(1,)]
        h = {"Authorization": "Bearer " + c.post("/auth/login", data={"username": "admin", "password": TEST_ADMIN_PASSWORD}).json()["access_token"]}
        sooner = c.post("/products", json={"product_name": "Eggs (crate)", "price": 4500, "quantity": 4,
                                           "date_added": str(TODAY), "expiry_date": str(TODAY + timedelta(days=5))}, headers=h).json()["batch_id"]
        later = c.post("/products", json={"product_name": "Eggs (crate)", "price": 4500, "quantity": 10,
                                          "date_added": str(TODAY), "expiry_date": str(TODAY + timedelta(days=20))}, headers=h).json()["batch_id"]
        qty = lambda: {p["batch_id"]: p["quantity"] for p in c.get("/products", headers=h).json()}  # noqa: E731
        sale = {"order_date": str(TODAY), "customer_name": "Mama Ade", "product_name": "Eggs (crate)",
                "cartons": 6, "amount": 27000, "amount_paid": 10000}

        o = c.post("/orders", json=sale, headers=h)                         # create + allocate
        assert o.status_code == 201, o.text
        assert [(t["batch_id"], t["quantity"]) for t in o.json()["stock_taken"]] == [(sooner, 4), (later, 2)]
        assert qty() == {sooner: 0, later: 8}
        oid = o.json()["id"]
        r = c.put(f"/orders/{oid}", json={**sale, "cartons": 3}, headers=h)  # correct down
        assert r.status_code == 200 and qty() == {sooner: 1, later: 10}
        r = c.put(f"/orders/{oid}", json={**sale, "cartons": 3, "amount_paid": 27000}, headers=h)  # mark paid
        assert r.status_code == 200 and r.json()["status"] == "Paid"
        assert c.delete(f"/products/{sooner}", headers=h).status_code == 409    # friendly app-level refusal
        assert c.delete(f"/orders/{oid}", headers=h).status_code == 204         # delete + restore stock
        assert qty() == {sooner: 4, later: 10}
        assert c.delete(f"/products/{sooner}", headers=h).status_code == 204    # no sales left: deletable
        assert rows("PRAGMA foreign_key_check") == []
    clear_test_database()


# ---------- migrations keep managing enforcement themselves ----------
def alembic_config(db_path):
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    cfg.attributes["skip_logging_config"] = True
    return cfg


def test_migrating_through_an_enforcing_connection_works_and_keeps_enforcement(tmp_path):
    """env.py turns enforcement off while migrating and restores it: an
    app-style connection with the pragma on must come back with it on."""
    path = tmp_path / "fresh.db"
    app_style = create_engine(f"sqlite:///{path}", poolclass=NullPool)
    assert enforce_foreign_keys(app_style) is True
    conn = app_style.connect()
    try:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
        conn.rollback()                                   # alembic needs a connection outside a transaction
        cfg = alembic_config(path)
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0002"
    finally:
        conn.close()
        app_style.dispose()


def test_alembic_command_line_path_is_unaffected(tmp_path):
    """Alembic's own engine (from DATABASE_URL) never gets the app's listener."""
    path = tmp_path / "cli.db"
    command.upgrade(alembic_config(path), "head")
    command.downgrade(alembic_config(path), "base")
    command.upgrade(alembic_config(path), "head")
    plain = create_engine(f"sqlite:///{path}", poolclass=NullPool)
    with plain.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0002"
    plain.dispose()
