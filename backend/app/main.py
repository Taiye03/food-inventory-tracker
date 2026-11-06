"""
FoodHeroes API. Run from the backend/ folder with:

    uvicorn app.main:app --reload

Then open http://localhost:8000/docs to try every endpoint in the browser.

The app never creates or changes database tables itself. Before the first run,
and after pulling code with a new migration, run `alembic upgrade head` (see
README.md and docs/DATABASE_RECOVERY.md). Startup refuses to serve requests
until the database is at the latest migration and has an administrator
(create the first one with `python -m scripts.create_admin`).
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.config import check_configuration
from app.cors import StartupConfiguredCORSMiddleware
from app.database import get_engine
from app.db_check import check_database_revision, check_has_administrator
from app.errors import validation_error_without_input
from app.routers import auth_router, orders_router, products_router, reports_router, users_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Refuse to start with a missing or unsafe SECRET_KEY, token lifetime or
    # CORS setting (P0-7). Raises ConfigurationError with what to fix.
    settings = check_configuration()   # includes DATABASE_URL: no default database
    engine = get_engine()
    # Refuse to start unless the database is at the latest migration. This
    # only reads; it never creates tables or runs migrations (P0-5 step 4).
    # A DatabaseNotReady error explains how to create or migrate the database.
    check_database_revision(engine)
    # No automatic admin and no default password (P0-7): refuse to start until
    # one exists. The first admin is created with scripts/create_admin.py.
    check_has_administrator(engine)
    # Only now, with every check passed, does CORS allow the validated origins
    # (app/cors.py reads this on each request). Cleared on shutdown.
    app.state.frontend_origins = settings.frontend_origins
    try:
        yield
    finally:
        app.state.frontend_origins = ()


app = FastAPI(title="FoodHeroes Stock Management API", lifespan=lifespan)
# 422 responses never repeat submitted values back, so never a password (app/errors.py).
app.add_exception_handler(RequestValidationError, validation_error_without_input)

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
