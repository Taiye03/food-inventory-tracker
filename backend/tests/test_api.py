"""
Run from backend/ with:  pytest -q
Uses a throwaway SQLite file so your real data is never touched
(tests/conftest.py fixes DATABASE_URL before the app is imported).
"""
import os
from datetime import date, timedelta

os.environ["DATABASE_URL"] = "sqlite:///./test_foodheroes.db"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from tests.db_helpers import TEST_ADMIN_PASSWORD, clear_test_database, reset_test_database  # noqa: E402

STAFF_PASSWORD = "staff password 1"   # meets the app/passwords.py rules


@pytest.fixture()
def client():
    reset_test_database()          # empty database at the latest migration
    with TestClient(app) as c:     # runs startup: checks the revision and that an admin exists
        yield c
    clear_test_database()


def login(client, username="admin", password=TEST_ADMIN_PASSWORD):
    r = client.post("/auth/login", data={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def product(days_until_expiry=30, **overrides):
    today = date.today()
    body = {
        "product_name": "Eggs (crate)",
        "price": 4500,
        "quantity": 20,
        "date_added": str(today),
        "expiry_date": str(today + timedelta(days=days_until_expiry)),
    }
    body.update(overrides)
    return body


def test_fresh_install_can_log_in(client):
    headers = login(client)
    me = client.get("/auth/me", headers=headers).json()
    assert me["username"] == "admin" and me["role"] == "Admin"


def test_wrong_password_rejected(client):
    r = client.post("/auth/login", data={"username": "admin", "password": "nope"})
    assert r.status_code == 401


def test_requires_login(client):
    assert client.get("/products").status_code == 401


def test_users_never_expose_passwords(client):
    users = client.get("/users", headers=login(client)).json()
    assert all("password" not in k for u in users for k in u)


def test_product_crud_and_batch_ids(client):
    h = login(client)
    first = client.post("/products", json=product(), headers=h).json()
    second = client.post("/products", json=product(), headers=h).json()
    prefix = f"BATCH-{date.today():%Y%m%d}-"
    assert first["batch_id"] == prefix + "001"
    assert second["batch_id"] == prefix + "002"

    r = client.put(f"/products/{first['batch_id']}", json=product(quantity=5), headers=h)
    assert r.status_code == 200 and r.json()["quantity"] == 5

    assert client.delete(f"/products/{first['batch_id']}", headers=h).status_code == 204
    assert client.get(f"/products/{first['batch_id']}", headers=h).status_code == 404
    assert client.put("/products/BATCH-NOPE-001", json=product(), headers=h).status_code == 404


def test_expiry_before_added_rejected(client):
    h = login(client)
    r = client.post("/products", json=product(days_until_expiry=-1, date_added=str(date.today())), headers=h)
    assert r.status_code == 422
    # The old app let updates skip this check; the API doesn't.
    created = client.post("/products", json=product(), headers=h).json()
    bad = product(expiry_date=str(date.today() - timedelta(days=5)))
    assert client.put(f"/products/{created['batch_id']}", json=bad, headers=h).status_code == 422


def test_expiry_alerts(client):
    h = login(client)
    past = str(date.today() - timedelta(days=10))
    client.post("/products", json=product(product_name="Old", date_added=past,
                                          expiry_date=str(date.today() - timedelta(days=1))), headers=h)
    client.post("/products", json=product(product_name="Soon", days_until_expiry=2), headers=h)
    client.post("/products", json=product(product_name="Fine", days_until_expiry=30), headers=h)
    alerts = {a["product_name"]: a["status"] for a in client.get("/products/alerts", headers=h).json()}
    assert alerts == {"Old": "expired", "Soon": "expiring_soon"}


def test_non_admin_can_view_but_not_change(client):
    admin = login(client)
    client.post("/users", json={"username": "staff", "fullname": "Staff Member", "role": "User",
                                "email": "staff@example.com", "password": STAFF_PASSWORD}, headers=admin)
    staff = login(client, "staff", STAFF_PASSWORD)
    assert client.get("/products", headers=staff).status_code == 200
    assert client.post("/products", json=product(), headers=staff).status_code == 403
    assert client.get("/users", headers=staff).status_code == 403


def test_bad_email_and_duplicate_username(client):
    h = login(client)
    bad = {"username": "x", "fullname": "X", "role": "User", "email": "not-an-email", "password": STAFF_PASSWORD}
    assert client.post("/users", json=bad, headers=h).status_code == 422
    dup = {**bad, "username": "admin", "email": "a@b.com"}
    assert client.post("/users", json=dup, headers=h).status_code == 400


def test_export_csv(client):
    h = login(client)
    client.post("/products", json=product(), headers=h)
    r = client.get("/products/export", headers=h)
    assert r.status_code == 200
    assert r.text.splitlines()[0] == "Batch ID,Product Name,Price,Quantity,Date Added,Expiry Date"


# ---------- Low stock ----------
def test_low_stock_adds_up_batches(client):
    h = login(client)
    client.post("/products", json=product(product_name="Eggs (crate)", quantity=4), headers=h)
    client.post("/products", json=product(product_name="Eggs (crate)", quantity=4), headers=h)
    client.post("/products", json=product(product_name="Eggs (tray)", quantity=50), headers=h)
    alerts = client.get("/products/low-stock", headers=h).json()
    assert [(a["product_name"], a["quantity"]) for a in alerts] == [("Eggs (crate)", 8)]


# ---------- Orders ----------
def stock(client, h, qty=100, name="Eggs (crate)", days=30):
    r = client.post("/products", json=product(product_name=name, quantity=qty, days_until_expiry=days), headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def order(**overrides):
    body = {"order_date": str(date.today()), "customer_name": "Mama Ade", "product_name": "Eggs (crate)",
            "cartons": 5, "amount": 22500, "amount_paid": 10000}
    body.update(overrides)
    return body


def test_orders_crud_and_status(client):
    h = login(client)
    stock(client, h)
    r = client.post("/orders", json=order(), headers=h)
    assert r.status_code == 201
    o = r.json()
    assert o["balance"] == 12500 and o["status"] == "Part paid"

    r = client.put(f"/orders/{o['id']}", json=order(amount_paid=22500), headers=h)
    assert r.json()["status"] == "Paid" and r.json()["balance"] == 0

    client.post("/orders", json=order(customer_name="Chika Stores", amount_paid=0), headers=h)
    unpaid = client.get("/orders?unpaid_only=true", headers=h).json()
    assert [x["customer_name"] for x in unpaid] == ["Chika Stores"]
    assert unpaid[0]["status"] == "Owing"
    assert len(client.get("/orders?customer=ade", headers=h).json()) == 1

    assert client.delete(f"/orders/{o['id']}", headers=h).status_code == 204
    assert client.delete(f"/orders/{o['id']}", headers=h).status_code == 404


def test_order_validation(client):
    h = login(client)
    assert client.post("/orders", json=order(amount_paid=99999), headers=h).status_code == 422
    assert client.post("/orders", json=order(cartons=0), headers=h).status_code == 422


def test_staff_can_record_but_not_edit_orders(client):
    admin = login(client)
    stock(client, admin)
    client.post("/users", json={"username": "staff", "fullname": "Staff", "role": "User",
                                "email": "s@example.com", "password": STAFF_PASSWORD}, headers=admin)
    staff = login(client, "staff", STAFF_PASSWORD)
    r = client.post("/orders", json=order(), headers=staff)
    assert r.status_code == 201
    assert client.put(f"/orders/{r.json()['id']}", json=order(), headers=staff).status_code == 403
    assert client.delete(f"/orders/{r.json()['id']}", headers=staff).status_code == 403


def test_order_cartons_can_be_blank_or_fractional(client):
    h = login(client)
    stock(client, h)
    r = client.post("/orders", json=order(cartons=None), headers=h)
    assert r.status_code == 201 and r.json()["cartons"] is None
    r = client.post("/orders", json=order(cartons=37.5), headers=h)
    assert r.status_code == 201 and r.json()["cartons"] == 37.5


def test_import_ledger(tmp_path):
    from app.database import SessionLocal
    from app.models import Order
    from scripts.import_ledger import main as import_main

    reset_test_database()
    ledger = tmp_path / "ledger.csv"
    ledger.write_text(
        "Date,Customer,Cartons (ctn),Amount (₦),Status,Notes\n"
        "2026-02-06,4u,38,2280000.0,Paid,\n"
        "2026-02-06,Pharm,2,120000.0,,\n"
        "2026-02-12,?,,53000.0,,Name unclear\n",
        encoding="utf-8",
    )
    import_main(str(ledger))
    import_main(str(ledger))  # second run must not duplicate
    db = SessionLocal()
    rows = {o.customer_name: o for o in db.query(Order).all()}
    db.close()
    clear_test_database()
    assert len(rows) == 3
    assert rows["4u"].amount_paid == 2280000
    assert rows["Pharmcare"].amount_paid == 0 and "Pharm" in rows["Pharmcare"].notes
    assert rows["Unknown"].cartons is None and rows["Unknown"].notes == "Name unclear"


# ---------- Reports ----------
def test_reports(client):
    h = login(client)
    stock(client, h)
    client.post("/orders", json=order(order_date="2026-01-05", customer_name="ACSK", cartons=None,
                                      amount=4000000, amount_paid=0), headers=h)
    client.post("/orders", json=order(order_date="2026-02-06", customer_name="4u", cartons=38,
                                      amount=2280000, amount_paid=2280000), headers=h)
    client.post("/orders", json=order(order_date="2026-02-12", customer_name="ACSK", cartons=None,
                                      amount=3000000, amount_paid=1000000), headers=h)

    months = client.get("/reports/monthly", headers=h).json()
    assert [(m["month"], m["orders"], m["sales"], m["owed"]) for m in months] == [
        ("2026-01", 1, 4000000, 4000000),
        ("2026-02", 2, 5280000, 2000000),
    ]
    assert months[1]["cartons"] == 38  # blank cartons count as 0, not an error

    customers = client.get("/reports/customers", headers=h).json()
    assert customers[0]["customer_name"] == "ACSK"
    assert customers[0]["sales"] == 7000000 and customers[0]["owed"] == 6000000
    assert customers[0]["last_order"] == "2026-02-12"


def test_reports_admin_only(client):
    admin = login(client)
    client.post("/users", json={"username": "staff", "fullname": "Staff", "role": "User",
                                "email": "s@example.com", "password": STAFF_PASSWORD}, headers=admin)
    staff = login(client, "staff", STAFF_PASSWORD)
    assert client.get("/reports/monthly", headers=staff).status_code == 403


# ---------- Orders take stock ----------
def test_sale_takes_soonest_expiring_batch_first(client):
    h = login(client)
    later = stock(client, h, qty=10, days=20)
    sooner = stock(client, h, qty=4, days=5)
    stock(client, h, qty=50, name="Eggs (tray)")
    r = client.post("/orders", json=order(cartons=6), headers=h)
    assert r.status_code == 201
    taken = [(t["batch_id"], t["quantity"]) for t in r.json()["stock_taken"]]
    assert taken == [(sooner["batch_id"], 4), (later["batch_id"], 2)]
    qty = {p["batch_id"]: p["quantity"] for p in client.get("/products", headers=h).json()}
    assert qty[sooner["batch_id"]] == 0 and qty[later["batch_id"]] == 8


def test_sale_blocked_when_not_enough_stock(client):
    h = login(client)
    b = stock(client, h, qty=6)
    r = client.post("/orders", json=order(cartons=10), headers=h)
    assert r.status_code == 400 and "Only 6" in r.json()["detail"]
    assert client.get("/orders", headers=h).json() == []                     # nothing saved
    assert client.get(f"/products/{b['batch_id']}", headers=h).json()["quantity"] == 6  # stock untouched
    r = client.post("/orders", json=order(product_name="Chicken"), headers=h)
    assert r.status_code == 400 and "no product called" in r.json()["detail"]


def test_expired_batches_are_never_sold(client):
    h = login(client)
    today = date.today()
    client.post("/products", json=product(quantity=50, date_added=str(today - timedelta(days=30)),
                                          expiry_date=str(today - timedelta(days=1))), headers=h)
    r = client.post("/orders", json=order(cartons=1), headers=h)
    assert r.status_code == 400 and "Only 0" in r.json()["detail"]


def test_edit_and_delete_put_stock_back(client):
    h = login(client)
    b = stock(client, h, qty=20)
    o = client.post("/orders", json=order(cartons=5), headers=h).json()
    qty = lambda: client.get(f"/products/{b['batch_id']}", headers=h).json()["quantity"]
    assert qty() == 15
    client.put(f"/orders/{o['id']}", json=order(cartons=8), headers=h)
    assert qty() == 12
    client.put(f"/orders/{o['id']}", json=order(cartons=8, amount_paid=22500), headers=h)  # just marking paid
    assert qty() == 12
    client.delete(f"/orders/{o['id']}", headers=h)
    assert qty() == 20


def test_half_cartons_and_blank_cartons(client):
    h = login(client)
    b = stock(client, h, qty=10)
    client.post("/orders", json=order(cartons=3.5), headers=h)
    client.post("/orders", json=order(cartons=None), headers=h)  # no carton count: stock unchanged
    assert client.get(f"/products/{b['batch_id']}", headers=h).json()["quantity"] == 6.5


def test_editing_imported_order_never_takes_stock(client):
    """Ledger orders were imported without taking stock; marking one paid mustn't change stock."""
    from app.database import SessionLocal
    from app.models import Order
    h = login(client)
    b = stock(client, h, qty=10)
    db = SessionLocal()
    imported = Order(order_date=date(2025, 10, 9), customer_name="Funtuna", product_name="Eggs (crate)",
                     cartons=50, amount=2653000, amount_paid=0)
    db.add(imported); db.commit(); oid = imported.id; db.close()
    r = client.put(f"/orders/{oid}", json=order(order_date="2025-10-09", customer_name="Funtuna", cartons=50,
                                                amount=2653000, amount_paid=2653000), headers=h)
    assert r.status_code == 200 and r.json()["status"] == "Paid"
    assert client.get(f"/products/{b['batch_id']}", headers=h).json()["quantity"] == 10


# ---------- Editing orders keeps stock history correct (P0-1) ----------
def batch_qty(client, h, batch_id):
    return client.get(f"/products/{batch_id}", headers=h).json()["quantity"]


def taken(r):
    return [(t["batch_id"], t["quantity"]) for t in r.json()["stock_taken"]]


def expire(batch_id):
    """Simulate time passing: the batch's expiry date is now in the past."""
    from app.database import SessionLocal
    from app.models import Product
    db = SessionLocal()
    p = db.query(Product).filter_by(batch_id=batch_id).one()
    p.date_added = date.today() - timedelta(days=30)
    p.expiry_date = date.today() - timedelta(days=1)
    db.commit()
    db.close()


def test_edit_sale_that_emptied_batch(client):
    h = login(client)
    b = stock(client, h, qty=5)
    o = client.post("/orders", json=order(cartons=5), headers=h).json()
    r = client.put(f"/orders/{o['id']}", json=order(cartons=5, amount_paid=22500), headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "Paid"
    assert taken(r) == [(b["batch_id"], 5)]
    assert batch_qty(client, h, b["batch_id"]) == 0


def test_change_quantity_of_sale_that_emptied_batch(client):
    h = login(client)
    b = stock(client, h, qty=5)
    o = client.post("/orders", json=order(cartons=5), headers=h).json()
    r = client.put(f"/orders/{o['id']}", json=order(cartons=4), headers=h)
    assert r.status_code == 200, r.text
    assert taken(r) == [(b["batch_id"], 4)]
    assert batch_qty(client, h, b["batch_id"]) == 1
    r = client.put(f"/orders/{o['id']}", json=order(cartons=5), headers=h)  # and back up again
    assert r.status_code == 200, r.text
    assert taken(r) == [(b["batch_id"], 5)]  # one allocation row, not two
    assert batch_qty(client, h, b["batch_id"]) == 0


def test_update_payment_after_batch_expires(client):
    h = login(client)
    b = stock(client, h, qty=10)
    o = client.post("/orders", json=order(cartons=3), headers=h).json()
    expire(b["batch_id"])
    r = client.put(f"/orders/{o['id']}", json=order(cartons=3, amount_paid=22500), headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "Paid"
    assert taken(r) == [(b["batch_id"], 3)]
    assert batch_qty(client, h, b["batch_id"]) == 7


def test_edit_customer_date_price_notes_leaves_stock_alone(client):
    h = login(client)
    first = stock(client, h, qty=4, days=5)
    later = stock(client, h, qty=10, days=20)
    o = client.post("/orders", json=order(cartons=6), headers=h).json()
    before = [(t["batch_id"], t["quantity"]) for t in o["stock_taken"]]
    r = client.put(f"/orders/{o['id']}", json=order(
        cartons=6, customer_name="Chika Stores", order_date=str(date.today() - timedelta(days=2)),
        amount=27000, amount_paid=5000, notes="Price corrected"), headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["customer_name"] == "Chika Stores"
    assert taken(r) == before
    assert batch_qty(client, h, first["batch_id"]) == 0
    assert batch_qty(client, h, later["batch_id"]) == 8


def test_correct_historical_sale_after_batch_expires(client):
    """Sold 20 from a batch that has since expired; it was really 15.
    The 5 go back to the original batch and stay unsellable; newer stock is untouched."""
    h = login(client)
    old = stock(client, h, qty=20, days=10)
    o = client.post("/orders", json=order(cartons=20), headers=h).json()
    new = stock(client, h, qty=10, days=30)
    expire(old["batch_id"])
    r = client.put(f"/orders/{o['id']}", json=order(cartons=15), headers=h)
    assert r.status_code == 200, r.text
    assert taken(r) == [(old["batch_id"], 15)]
    assert batch_qty(client, h, old["batch_id"]) == 5    # returned, but expired
    assert batch_qty(client, h, new["batch_id"]) == 10   # newer stock untouched
    r = client.post("/orders", json=order(cartons=11), headers=h)
    assert r.status_code == 400 and "Only 10" in r.json()["detail"]  # the 5 aren't sellable


def test_increasing_a_sale_takes_only_the_extra(client):
    """Raising a historical sale keeps its original allocation; only the extra
    comes from today's sellable stock."""
    h = login(client)
    old = stock(client, h, qty=8, days=10)
    o = client.post("/orders", json=order(cartons=8), headers=h).json()
    new = stock(client, h, qty=10, days=30)
    expire(old["batch_id"])
    r = client.put(f"/orders/{o['id']}", json=order(cartons=11), headers=h)
    assert r.status_code == 200, r.text
    assert taken(r) == [(old["batch_id"], 8), (new["batch_id"], 3)]
    assert batch_qty(client, h, new["batch_id"]) == 7
    r = client.put(f"/orders/{o['id']}", json=order(cartons=30), headers=h)
    assert r.status_code == 400 and "19 more" in r.json()["detail"]
    assert batch_qty(client, h, new["batch_id"]) == 7    # blocked edit changed nothing


def test_changing_product_reallocates_from_new_product(client):
    h = login(client)
    crate = stock(client, h, qty=10)
    tray = stock(client, h, qty=10, name="Eggs (tray)")
    o = client.post("/orders", json=order(cartons=4), headers=h).json()
    r = client.put(f"/orders/{o['id']}", json=order(cartons=4, product_name="Eggs (tray)"), headers=h)
    assert r.status_code == 200, r.text
    assert taken(r) == [(tray["batch_id"], 4)]
    assert batch_qty(client, h, crate["batch_id"]) == 10
    assert batch_qty(client, h, tray["batch_id"]) == 6


# ---------- Low stock counts only sellable stock (P0-2) ----------
def test_low_stock_ignores_expired_batches(client):
    """50 expired cartons must not hide that only 2 can actually be sold."""
    h = login(client)
    old = stock(client, h, qty=50, days=10)
    stock(client, h, qty=2, days=20)
    expire(old["batch_id"])
    alerts = client.get("/products/low-stock", headers=h).json()
    assert [(a["product_name"], a["quantity"]) for a in alerts] == [("Eggs (crate)", 2)]


def test_low_stock_when_every_batch_has_expired(client):
    """A product whose stock has all expired has nothing to sell: alert at 0."""
    h = login(client)
    old = stock(client, h, qty=30, days=10)
    expire(old["batch_id"])
    alerts = client.get("/products/low-stock", headers=h).json()
    assert [(a["product_name"], a["quantity"]) for a in alerts] == [("Eggs (crate)", 0)]


def test_low_stock_matches_what_a_sale_can_take(client):
    """The alert and the sale check must agree on how much is sellable."""
    h = login(client)
    old = stock(client, h, qty=40, days=10)
    stock(client, h, qty=8, days=20)
    expire(old["batch_id"])
    alerts = client.get("/products/low-stock", headers=h).json()
    assert len(alerts) == 1, alerts
    shown = alerts[0]["quantity"]
    assert client.post("/orders", json=order(cartons=shown), headers=h).status_code == 201
    r = client.post("/orders", json=order(cartons=1), headers=h)
    assert r.status_code == 400 and "Only 0" in r.json()["detail"]


def test_batch_expiring_today_is_sellable(client):
    """Last day is still a selling day: alerts and sales both count today's batch."""
    h = login(client)
    b = stock(client, h, qty=6, days=0)  # expires today
    alerts = client.get("/products/low-stock", headers=h).json()
    assert [(a["product_name"], a["quantity"]) for a in alerts] == [("Eggs (crate)", 6)]
    r = client.post("/orders", json=order(cartons=6), headers=h)
    assert r.status_code == 201, r.text
    assert taken(r) == [(b["batch_id"], 6)]
    alerts = client.get("/products/low-stock", headers=h).json()
    assert [(a["product_name"], a["quantity"]) for a in alerts] == [("Eggs (crate)", 0)]


# ---------- Batches used by sales can't be deleted (P0-3) ----------
def test_cannot_delete_batch_used_by_a_sale(client):
    h = login(client)
    b = stock(client, h, qty=10)
    o = client.post("/orders", json=order(cartons=4), headers=h).json()
    r = client.delete(f"/products/{b['batch_id']}", headers=h)
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == (
        "This inventory batch cannot be deleted because it has been used in "
        "recorded sales. Review the associated sales before making corrections."
    )
    assert batch_qty(client, h, b["batch_id"]) == 6                      # batch still there
    assert client.delete(f"/orders/{o['id']}", headers=h).status_code == 204
    assert batch_qty(client, h, b["batch_id"]) == 10                     # cartons returned, none lost


def test_cannot_delete_emptied_batch_used_by_a_sale(client):
    """Quantity 0 doesn't make a batch safe to delete: its sales still point at it."""
    h = login(client)
    b = stock(client, h, qty=5)
    client.post("/orders", json=order(cartons=5), headers=h)
    assert batch_qty(client, h, b["batch_id"]) == 0
    assert client.delete(f"/products/{b['batch_id']}", headers=h).status_code == 409
    assert client.get(f"/products/{b['batch_id']}", headers=h).status_code == 200


def test_cannot_delete_expired_batch_used_by_a_corrected_sale(client):
    """A historical sale, corrected after its batch expired, still came from that batch."""
    h = login(client)
    b = stock(client, h, qty=20, days=10)
    o = client.post("/orders", json=order(cartons=20), headers=h).json()
    expire(b["batch_id"])
    client.put(f"/orders/{o['id']}", json=order(cartons=15), headers=h)  # 5 returned
    assert client.delete(f"/products/{b['batch_id']}", headers=h).status_code == 409
    assert batch_qty(client, h, b["batch_id"]) == 5


def test_can_delete_batch_no_sale_took_from(client):
    """Unsold batches (e.g. entered by mistake) can still be deleted, even when
    other batches of the same product have sales."""
    h = login(client)
    sold = stock(client, h, qty=10, days=5)
    unsold = stock(client, h, qty=10, days=30)
    client.post("/orders", json=order(cartons=3), headers=h)  # takes from `sold` only
    assert client.delete(f"/products/{unsold['batch_id']}", headers=h).status_code == 204
    assert client.delete(f"/products/{sold['batch_id']}", headers=h).status_code == 409
