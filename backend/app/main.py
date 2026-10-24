"""
FoodHeroes API. Run from the backend/ folder with:

    uvicorn app.main:app --reload

Then open http://localhost:8000/docs to try every endpoint in the browser.
"""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import inspect, text
from fastapi.middleware.cors import CORSMiddleware

from app.auth import hash_password
from app.database import DATABASE_URL, Base, SessionLocal, engine
from app.models import User
from app.routers import auth_router, orders_router, products_router, users_router


def seed_admin():
    """
    Fixes the old init_users_csv() bug: on a brand-new install there's no one
    to log in as, so create one admin account. Change this password right away.
    """
    db = SessionLocal()
    try:
        if db.query(User).count() == 0:
            db.add(User(
                username="admin",
                fullname="Administrator",
                role="Admin",
                email="admin@example.com",
                password_hash=hash_password(os.getenv("ADMIN_PASSWORD", "changeme123")),
            ))
            db.commit()
    finally:
        db.close()


def upgrade_orders_table():
    """
    Small one-off migration. The first version of the orders table required
    a whole number of cartons. The real ledger has blanks and halves
    (e.g. 37.5), so cartons became an optional decimal. SQLite can't change
    a column in place, so rebuild the table and copy any existing orders.
    """
    if not DATABASE_URL.startswith("sqlite"):
        return
    insp = inspect(engine)
    if "orders" not in insp.get_table_names():
        return
    cartons = next(c for c in insp.get_columns("orders") if c["name"] == "cartons")
    if cartons["nullable"]:
        return  # already upgraded
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE orders RENAME TO orders_old"))
        conn.execute(text("DROP INDEX IF EXISTS ix_orders_id"))
        conn.execute(text("DROP INDEX IF EXISTS ix_orders_order_date"))
        conn.execute(text("DROP INDEX IF EXISTS ix_orders_customer_name"))
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO orders SELECT * FROM orders_old"))
        conn.execute(text("DROP TABLE orders_old"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    upgrade_orders_table()
    Base.metadata.create_all(bind=engine)  # create tables if they don't exist
    seed_admin()
    yield


app = FastAPI(title="FoodHeroes Stock Management API", lifespan=lifespan)

# Lets the React dev server (port 5173) call this API (port 8000).
FRONTEND_ORIGINS = os.getenv("FRONTEND_ORIGINS", "http://localhost:5173").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router.router)
app.include_router(users_router.router)
app.include_router(products_router.router)
app.include_router(orders_router.router)


@app.get("/")
def health():
    return {"status": "ok"}
