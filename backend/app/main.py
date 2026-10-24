"""
FoodHeroes API. Run from the backend/ folder with:

    uvicorn app.main:app --reload

Then open http://localhost:8000/docs to try every endpoint in the browser.
"""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth import hash_password
from app.database import Base, SessionLocal, engine
from app.models import User
from app.routers import auth_router, products_router, users_router


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


@app.get("/")
def health():
    return {"status": "ok"}
