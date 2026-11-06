"""
Deleting an order with SQLite foreign-key enforcement ON (P0-5 step 5).

With enforcement on, the database refuses to delete an order while
order_allocations rows still point at it, exactly as PostgreSQL will. These
tests run every deletion that way, using the throwaway test database.
"""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.database import engine
from app.main import app
from tests.db_helpers import clear_test_database, foreign_keys_enforced, reset_test_database

TODAY = date.today()


@pytest.fixture()
def client():
    with foreign_keys_enforced():
        reset_test_database()
        with TestClient(app) as c:
            yield c
        clear_test_database()


@pytest.fixture()
def h(client):
    r = client.post("/auth/login", data={"username": "admin", "password": "changeme123"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def add_batch(client, h, qty, days, name="Eggs (crate)"):
    r = client.post("/products", json={
        "product_name": name, "price": 4500, "quantity": qty, "date_added": str(TODAY),
        "expiry_date": str(TODAY + timedelta(days=days))}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["batch_id"]


def sell(client, h, cartons, amount, paid, customer="Mama Ade", on=TODAY):
    r = client.post("/orders", json={
        "order_date": str(on), "customer_name": customer, "product_name": "Eggs (crate)",
        "cartons": cartons, "amount": amount, "amount_paid": paid}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def stock(client, h):
    return {p["batch_id"]: p["quantity"] for p in client.get("/products", headers=h).json()}


def orders(client, h):
    return {o["id"]: o for o in client.get("/orders", headers=h).json()}


def db_rows(sql):
    with engine.connect() as conn:
        return conn.execute(text(sql)).fetchall()


def add_imported_order():
    """A ledger order recorded without taking stock (no allocations)."""
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO orders (order_date, customer_name, product_name, cartons, amount, amount_paid, notes) "
                          "VALUES ('2026-02-12', 'ACSK', 'Eggs (carton)', NULL, 3000000, 1000000, 'imported')"))


# ---------- the setup really enforces foreign keys ----------
def test_enforcement_is_on(client):
    assert db_rows("PRAGMA foreign_keys") == [(1,)]
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO order_allocations (order_id, product_id, batch_id, quantity) "
                              "VALUES (999, NULL, 'X', 1)"))


# ---------- deleting orders that took stock ----------
@pytest.mark.parametrize("paid, status", [(27000, "Paid"), (10000, "Part paid"), (0, "Owing")])
def test_delete_order_returns_stock_and_leaves_everything_else(client, h, paid, status):
    sooner = add_batch(client, h, qty=4, days=5)
    later = add_batch(client, h, qty=10, days=20)
    other = sell(client, h, cartons=2, amount=9000, paid=4000, customer="Chika Stores")   # takes 2 from `sooner`
    target = sell(client, h, cartons=6, amount=27000, paid=paid)                          # 2 from sooner, 4 from later
    add_imported_order()
    assert target["status"] == status
    assert [(t["batch_id"], t["quantity"]) for t in target["stock_taken"]] == [(sooner, 2), (later, 4)]
    assert stock(client, h) == {sooner: 0, later: 6}
    before = orders(client, h)

    r = client.delete(f"/orders/{target['id']}", headers=h)
    assert r.status_code == 204, r.text

    assert stock(client, h) == {sooner: 2, later: 10}                 # exactly the 6 cartons returned
    after = orders(client, h)
    assert target["id"] not in after
    assert {k: v for k, v in before.items() if k != target["id"]} == after   # other orders untouched,
    assert after[other["id"]]["balance"] == 5000                     # including balances and stock_taken
    assert db_rows(f"SELECT COUNT(*) FROM order_allocations WHERE order_id = {target['id']}") == [(0,)]
    assert db_rows("SELECT order_id, batch_id, quantity FROM order_allocations") == [(other["id"], sooner, 2.0)]
    assert db_rows("PRAGMA foreign_key_check") == []


def test_delete_order_whose_batch_has_expired(client, h):
    """Stock goes back to the original batch even though it has expired (and stays unsellable)."""
    batch = add_batch(client, h, qty=10, days=10)
    o = sell(client, h, cartons=4, amount=18000, paid=0)
    with engine.begin() as conn:
        conn.execute(text("UPDATE products SET date_added = :a, expiry_date = :e WHERE batch_id = :b"),
                     {"a": str(TODAY - timedelta(days=30)), "e": str(TODAY - timedelta(days=1)), "b": batch})
    assert client.delete(f"/orders/{o['id']}", headers=h).status_code == 204
    assert stock(client, h) == {batch: 10}
    assert db_rows("SELECT COUNT(*) FROM order_allocations") == [(0,)]


def test_delete_imported_order_without_allocations(client, h):
    batch = add_batch(client, h, qty=10, days=10)
    add_imported_order()
    (imported_id,) = [i for i, o in orders(client, h).items() if o["notes"] == "imported"]
    assert client.delete(f"/orders/{imported_id}", headers=h).status_code == 204
    assert stock(client, h) == {batch: 10}                            # imported orders never touched stock


def test_delete_one_of_several_orders_on_the_same_batch(client, h):
    batch = add_batch(client, h, qty=20, days=10)
    first, second, third = (sell(client, h, cartons=c, amount=c * 4500, paid=0, customer=n)
                            for c, n in ((3, "A"), (5, "B"), (2.5, "C")))
    assert stock(client, h) == {batch: 9.5}
    assert client.delete(f"/orders/{second['id']}", headers=h).status_code == 204
    assert stock(client, h) == {batch: 14.5}
    assert sorted(db_rows("SELECT order_id, quantity FROM order_allocations")) == [(first["id"], 3.0), (third["id"], 2.5)]


# ---------- a failed deletion changes nothing ----------
def test_failed_delete_rolls_back_everything(client, h):
    """Make the database refuse the final DELETE of the order itself, after stock has
    been returned and allocations removed in the same transaction: all of it must undo."""
    sooner = add_batch(client, h, qty=4, days=5)
    later = add_batch(client, h, qty=10, days=20)
    o = sell(client, h, cartons=6, amount=27000, paid=10000)
    stock_before = stock(client, h)
    allocations_before = db_rows("SELECT id, order_id, product_id, batch_id, quantity FROM order_allocations ORDER BY id")
    with engine.begin() as conn:
        conn.execute(text("CREATE TRIGGER refuse_order_delete BEFORE DELETE ON orders "
                          "BEGIN SELECT RAISE(ABORT, 'simulated failure deleting the order'); END"))
    try:
        with pytest.raises(Exception, match="simulated failure"):
            client.delete(f"/orders/{o['id']}", headers=h)
    finally:
        with engine.begin() as conn:
            conn.execute(text("DROP TRIGGER refuse_order_delete"))

    assert stock(client, h) == stock_before == {sooner: 0, later: 8}  # no partially returned stock
    assert db_rows("SELECT id, order_id, product_id, batch_id, quantity FROM order_allocations ORDER BY id") == allocations_before
    assert orders(client, h)[o["id"]]["balance"] == 17000             # order still there, unchanged
    assert db_rows("PRAGMA foreign_key_check") == []
    assert client.delete(f"/orders/{o['id']}", headers=h).status_code == 204   # and deletable once the cause is gone
    assert stock(client, h) == {sooner: 4, later: 10}
