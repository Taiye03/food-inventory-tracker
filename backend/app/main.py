"""
FoodHeroes API. Run from the backend/ folder with:

    uvicorn app.main:app --reload

Then open http://localhost:8000/docs to try every endpoint in the browser.

The app never creates or changes database tables itself. Before the first run,
and after pulling code with a new migration, run `alembic upgrade head` (see
README.md and docs/DATABASE_RECOVERY.md). Startup refuses to serve requests
until the database is at the latest migration.
"""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.auth import hash_password
from app.config import check_configuration
from app.cors import StartupConfiguredCORSMiddleware
from app.database import SessionLocal, engine
from app.db_check import check_database_revision
from app.models import User
from app.routers import auth_router, orders_router, products_router, reports_router, users_router


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Refuse to start with a missing or unsafe SECRET_KEY, token lifetime or
    # CORS setting (P0-7). Raises ConfigurationError with what to fix.
    settings = check_configuration()
    # Refuse to start unless the database is at the latest migration. This
    # only reads; it never creates tables or runs migrations (P0-5 step 4).
    # A DatabaseNotReady error explains how to create or migrate the database.
    check_database_revision(engine)
    seed_admin()  # data, not schema: only once the schema is known to be right
    # Only now, with every check passed, does CORS allow the validated origins
    # (app/cors.py reads this on each request). Cleared on shutdown.
    app.state.frontend_origins = settings.frontend_origins
    try:
        yield
    finally:
        app.state.frontend_origins = ()


app = FastAPI(title="FoodHeroes Stock Management API", lifespan=lifespan)

# Lets the React dev server (port 5173) call this API (port 8000). Allowed
# origins are the ones validated at startup, not read here at import.
app.state.frontend_origins = ()
app.add_middleware(StartupConfiguredCORSMiddleware, state=app.state)

app.include_router(auth_router.router)
app.include_router(users_router.router)
app.include_router(products_router.router)
app.include_router(orders_router.router)
app.include_router(reports_router.router)


@app.get("/")
def health():
    return {"status": "ok"}
