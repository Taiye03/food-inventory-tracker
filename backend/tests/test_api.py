"""
Run from backend/ with:  pytest -q
Uses a throwaway SQLite file so your real data is never touched.
"""
import os
from datetime import date, timedelta

os.environ["DATABASE_URL"] = "sqlite:///./test_foodheroes.db"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    with TestClient(app) as c:  # runs startup: creates tables + seeds admin
        yield c
    Base.metadata.drop_all(bind=engine)


def login(client, username="admin", password="changeme123"):
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
                                "email": "staff@example.com", "password": "secret1"}, headers=admin)
    staff = login(client, "staff", "secret1")
    assert client.get("/products", headers=staff).status_code == 200
    assert client.post("/products", json=product(), headers=staff).status_code == 403
    assert client.get("/users", headers=staff).status_code == 403


def test_bad_email_and_duplicate_username(client):
    h = login(client)
    bad = {"username": "x", "fullname": "X", "role": "User", "email": "not-an-email", "password": "secret1"}
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
def order(**overrides):
    body = {"order_date": str(date.today()), "customer_name": "Mama Ade", "product_name": "Eggs (crate)",
            "cartons": 5, "amount": 22500, "amount_paid": 10000}
    body.update(overrides)
    return body


def test_orders_crud_and_status(client):
    h = login(client)
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
    client.post("/users", json={"username": "staff", "fullname": "Staff", "role": "User",
                                "email": "s@example.com", "password": "secret1"}, headers=admin)
    staff = login(client, "staff", "secret1")
    r = client.post("/orders", json=order(), headers=staff)
    assert r.status_code == 201
    assert client.put(f"/orders/{r.json()['id']}", json=order(), headers=staff).status_code == 403
    assert client.delete(f"/orders/{r.json()['id']}", headers=staff).status_code == 403


def test_order_cartons_can_be_blank_or_fractional(client):
    h = login(client)
    r = client.post("/orders", json=order(cartons=None), headers=h)
    assert r.status_code == 201 and r.json()["cartons"] is None
    r = client.post("/orders", json=order(cartons=37.5), headers=h)
    assert r.status_code == 201 and r.json()["cartons"] == 37.5


def test_import_ledger(tmp_path):
    from app.database import Base, SessionLocal, engine
    from app.models import Order
    from scripts.import_ledger import main as import_main

    Base.metadata.drop_all(bind=engine)
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
    Base.metadata.drop_all(bind=engine)
    assert len(rows) == 3
    assert rows["4u"].amount_paid == 2280000
    assert rows["Pharmcare"].amount_paid == 0 and "Pharm" in rows["Pharmcare"].notes
    assert rows["Unknown"].cartons is None and rows["Unknown"].notes == "Name unclear"


# ---------- Reports ----------
def test_reports(client):
    h = login(client)
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
                                "email": "s@example.com", "password": "secret1"}, headers=admin)
    staff = login(client, "staff", "secret1")
    assert client.get("/reports/monthly", headers=staff).status_code == 403
