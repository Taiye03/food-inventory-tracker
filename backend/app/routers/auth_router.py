"""Replaces login() in loginpage.py."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.auth import create_token, get_current_user, verify_password
from app.database import get_db
from app.models import User
from app.schemas import Token, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=Token)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == form.username).first()
    if not user or not verify_password(form.password, user.password_hash):
        # Same message for both cases so nobody can probe which usernames exist.
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    return Token(access_token=create_token(user.username, user.role))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    """Who am I? The frontend uses this to know whether to show admin buttons."""
    return user
