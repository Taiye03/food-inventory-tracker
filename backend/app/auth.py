"""
Login, password hashing and role checks.

- Passwords are hashed with bcrypt (the old app stored them as plain text).
- A successful login returns a JWT "token". The React app sends it back on
  every request in an `Authorization: Bearer <token>` header.
- `require_admin` is what actually enforces roles: a "User" account gets a
  403 error if it tries to change anything.
"""
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.config import signing_key, token_minutes
from app.database import get_db
from app.models import User

# The signing key and token lifetime come from app/config.py, which refuses a
# missing, default or weak SECRET_KEY. There is deliberately no fallback key.
ALGORITHM = "HS256"

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_token(username: str, role: str) -> str:
    expires = datetime.now(timezone.utc) + timedelta(minutes=token_minutes())
    return jwt.encode({"sub": username, "role": role, "exp": expires}, signing_key(), algorithm=ALGORITHM)


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not logged in or session expired",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, signing_key(), algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        raise unauthorized
    user = db.query(User).filter(User.username == payload.get("sub")).first()
    if user is None:
        raise unauthorized
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "Admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admins only")
    return user
