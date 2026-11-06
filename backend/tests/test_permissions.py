"""
P0-8 step 1: who may call each endpoint.

ENDPOINTS below is the written-down access rule for every route in the API,
independent of app/permissions.py. The tests check that each role gets
exactly that access (not logged in -> 401, not allowed -> 403 and nothing
changes, allowed -> the endpoint runs), and that every route declares a
permission in app/permissions.py, so a new endpoint can't ship unprotected.

All on the throwaway test database.
"""
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.auth import create_token, hash_password
from app.database import engine
from app.main import app
from app.routers import auth_router, orders_router, products_router, reports_router, users_router
from tests.db_helpers import TEST_ADMIN_PASSWORD, clear_test_database, reset_test_database

STAFF_PASSWORD = "staff password 1"
_STAFF_HASH = hash_password(STAFF_PASSWORD)          # hashed once: bcrypt is slow on purpose
TODAY = date.today()

PUBLIC, ANY, ADMIN = "public", "any logged-in user", "admin only"


def new_batch(name="Eggs (crate)", qty=10):
    return {"product_name": name, "price": 4500, "quantity": qty, "date_added": str(TODAY),
            "expiry_date": str(TODAY + timedelta(days=10))}


def new_order(cartons=1):
    return {"order_date": str(TODAY), "customer_name": "Mama Ade", "product_name": "Eggs (crate)",
            "cartons": cartons, "amount": 4500 * cartons, "amount_paid": 0}


def new_user():
    return {"username": "newstaff", "fullname": "New Staff", "role": "User",
            "email": "new@example.com", "password": "new staff password"}


# (method, path, access, json body) -- {batch} and {order} are filled in per test
ENDPOINTS = [
    ("GET", "/", PUBLIC, None),
    ("POST", "/auth/login", PUBLIC, None),
    ("GET", "/auth/me", ANY, None),
    ("GET", "/users", ADMIN, None),
    ("POST", "/users", ADMIN, new_user),
    ("GET", "/products", ANY, None),
    ("GET", "/products/alerts", ANY, None),
    ("GET", "/products/low-stock", ANY, None),
    ("GET", "/products/export", ANY, None),
    ("GET", "/products/{batch}", ANY, None),
    ("POST", "/products", ADMIN, new_batch),
    ("PUT", "/products/{batch}", ADMIN, lambda: new_batch(qty=12)),
    ("DELETE", "/products/{spare_batch}", ADMIN, None),
    ("GET", "/orders", ANY, None),
    ("POST", "/orders", ANY, new_order),
    ("PUT", "/orders/{order}", ADMIN, lambda: new_order(cartons=2)),
    ("DELETE", "/orders/{order}", ADMIN, None),
    ("GET", "/reports/monthly", ADMIN, None),
    ("GET", "/reports/customers", ADMIN, None),
]
PROTECTED = [e for e in ENDPOINTS if e[2] != PUBLIC]


def params(rows):
    return [pytest.param(*row, id=f"{row[0]} {row[1]}") for row in rows]
ROUTE_PATHS = {  # how each test path is written in the app's routes
    "/products/{batch}": "/products/{batch_id}", "/products/{spare_batch}": "/products/{batch_id}",
    "/orders/{order}": "/orders/{order_id}",
}


@pytest.fixture()
def world():
    """Test admin, one staff account, a batch with one sale, and a spare batch."""
    reset_test_database()
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO users (username, fullname, role, email, password_hash) "
                          "VALUES ('staff', 'Staff', 'User', 's@example.com', :h)"), {"h": _STAFF_HASH})
    with TestClient(app) as c:
        admin = bearer("admin", "Admin")
        batch = c.post("/products", json=new_batch(qty=20), headers=admin).json()["batch_id"]
        spare = c.post("/products", json=new_batch(name="Eggs (tray)"), headers=admin).json()["batch_id"]
        order = c.post("/orders", json=new_order(), headers=admin).json()["id"]
        yield {"client": c, "Admin": admin, "User": bearer("staff", "User"),
               "ids": {"batch": batch, "spare_batch": spare, "order": order}}
    clear_test_database()


def bearer(username, role):
    """A token exactly as /auth/login issues one (same function), without the bcrypt check."""
    return {"Authorization": f"Bearer {create_token(username, role)}"}


def login(c, username, password):
    r = c.post("/auth/login", data={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def call(world, method, path, body, headers=None):
    url = path.format(**world["ids"])
    kwargs = {"headers": headers or {}}
    if body is not None:
        kwargs["json"] = body()
    if path == "/auth/login":
        kwargs["data"] = {"username": "admin", "password": TEST_ADMIN_PASSWORD}
    return world["client"].request(method, url, **kwargs)


def snapshot():
    con = sqlite3.connect(Path(engine.url.database))
    try:
        return {t: con.execute(f"SELECT * FROM {t} ORDER BY id").fetchall()
                for t in ("users", "products", "orders", "order_allocations")}
    finally:
        con.close()


# ---------- each role gets exactly the documented access ----------
@pytest.mark.parametrize("method, path, access, body", params(PROTECTED))
@pytest.mark.parametrize("credentials", ["no token", "garbage token", "token signed with another key"])
def test_not_logged_in_is_rejected_with_401(world, credentials, method, path, access, body):
    import jwt
    headers = {"no token": {},
               "garbage token": {"Authorization": "Bearer not-a-token"},
               "token signed with another key": {"Authorization": "Bearer " + jwt.encode(
                   {"sub": "admin", "role": "Admin"}, "another key that is long enough 1234567", algorithm="HS256")},
               }[credentials]
    before = snapshot()
    r = call(world, method, path, body, headers)
    assert r.status_code == 401, r.text
    assert r.headers.get("www-authenticate") == "Bearer"
    assert snapshot() == before


@pytest.mark.parametrize("method, path, access, body", params(PROTECTED))
@pytest.mark.parametrize("role", ["Admin", "User"])
def test_each_role_gets_exactly_its_access(world, role, method, path, access, body):
    allowed = role == "Admin" or access == ANY
    before = snapshot()
    r = call(world, method, path, body, world[role])
    if allowed:
        assert r.status_code not in (401, 403) and r.status_code < 500, r.text
    else:
        assert r.status_code == 403, r.text
        assert r.json() == {"detail": "Admins only"}                 # unchanged message
        assert snapshot() == before                                  # a refused request changes nothing


@pytest.mark.parametrize("method, path, access, body", params(e for e in ENDPOINTS if e[2] == PUBLIC))
def test_public_endpoints_need_no_login(world, method, path, access, body):
    assert call(world, method, path, body).status_code == 200


def test_the_existing_admin_account_can_do_everything(world):
    """The admin account logs in for real and still reaches every endpoint."""
    admin = login(world["client"], "admin", TEST_ADMIN_PASSWORD)
    for method, path, access, body in PROTECTED:
        r = call(world, method, path, body, admin)
        assert r.status_code in (200, 201, 204), (method, path, r.status_code, r.text)


def test_a_real_staff_login_gets_staff_access(world):
    staff = login(world["client"], "staff", STAFF_PASSWORD)
    assert world["client"].get("/products", headers=staff).status_code == 200
    assert world["client"].get("/users", headers=staff).status_code == 403


# ---------- every route declares its permission ----------
def api_routes():
    routers = (auth_router, users_router, products_router, orders_router, reports_router)
    routes = [r for m in routers for r in m.router.routes if isinstance(r, APIRoute)]
    routes += [r for r in app.routes if isinstance(r, APIRoute)]
    return routes


def declared_permissions(route):
    """Every permission required anywhere in the route's dependency tree."""
    found, stack = [], list(route.dependant.dependencies)
    while stack:
        d = stack.pop()
        if getattr(d.call, "required_permission", None) is not None:
            found.append(d.call.required_permission)
        stack.extend(d.dependencies)
    return found


def test_the_table_above_covers_every_route_in_the_app():
    in_app = {(m.upper(), p) for p, ops in app.openapi()["paths"].items() for m in ops}
    inspected = {(m, r.path) for r in api_routes() for m in r.methods}
    in_table = {(m, ROUTE_PATHS.get(p, p)) for m, p, _, _ in ENDPOINTS}
    assert in_app == inspected == in_table


def test_every_protected_route_declares_exactly_one_permission():
    from app.permissions import Permission
    public = {(m, p) for m, p, a, _ in ENDPOINTS if a == PUBLIC}
    for route in api_routes():
        for method in route.methods:
            perms = declared_permissions(route)
            if (method, route.path) in public:
                assert perms == [], (method, route.path)
            else:
                assert len(perms) == 1 and isinstance(perms[0], Permission), (method, route.path, perms)


def test_declared_permissions_match_the_table():
    """The permission each route declares gives exactly the access the table says."""
    from app.permissions import ROLE_PERMISSIONS
    expected = {(m, ROUTE_PATHS.get(p, p)): a for m, p, a, _ in PROTECTED}
    for route in api_routes():
        for method in route.methods:
            if (method, route.path) not in expected:
                continue
            (perm,) = declared_permissions(route)
            roles = {role for role, perms in ROLE_PERMISSIONS.items() if perm in perms}
            assert roles == ({"Admin", "User"} if expected[(method, route.path)] == ANY else {"Admin"}), \
                (method, route.path, perm, roles)


# ---------- the permission table itself ----------
def test_admin_has_every_permission_and_user_has_only_the_current_ones():
    from app.permissions import ROLE_PERMISSIONS, Permission
    assert set(ROLE_PERMISSIONS) == {"Admin", "User"}
    assert ROLE_PERMISSIONS["Admin"] == frozenset(Permission)
    assert ROLE_PERMISSIONS["User"] == {
        Permission.VIEW_OWN_ACCOUNT, Permission.VIEW_STOCK, Permission.EXPORT_STOCK,
        Permission.VIEW_ORDERS, Permission.RECORD_SALE}


@pytest.mark.parametrize("role", ["admin", "ADMIN", "Owner", "", None, "User "])
def test_unknown_roles_have_no_permissions(role):
    from app.permissions import Permission, has_permission
    assert not any(has_permission(role, p) for p in Permission)
