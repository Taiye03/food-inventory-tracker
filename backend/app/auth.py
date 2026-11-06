"""
Login and password hashing.

- Passwords are hashed with bcrypt (the old app stored them as plain text).
- A successful login returns a JWT "token". The React app sends it back on
  every request in an `Authorization: Bearer <token>` header.
- `get_current_user` turns that token into the logged-in User (401 if it's
  missing, invalid or expired). What that user may then do is decided in
  app/permissions.py: every endpoint uses require_permission(...), never
  this module, to check access.
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
from app.passwords import MAX_BYTES, PasswordRejected, too_long_for_bcrypt

# The signing key and token lifetime come from app/config.py, which refuses a
# missing, default or weak SECRET_KEY. There is deliberately no fallback key.
ALGORITHM = "HS256"

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def hash_password(plain: str) -> str:
    """Hash a NEW password. Callers check app/passwords.py rules first; this
    refuses anything bcrypt can't use in full rather than silently cutting it."""
    if too_long_for_bcrypt(plain):
        raise PasswordRejected(f"The password is too long: bcrypt uses at most {MAX_BYTES} bytes.")
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """Check a login attempt.

    An attempt longer than bcrypt's 72 bytes is simply wrong: no account can
    have such a password (they're refused when set, and bcrypt 5, which this
    app has always installed, refuses to hash them). It's rejected here, before
    bcrypt, so it gets a normal 401 instead of bcrypt 5's error (which used to
    be a 500), and is never cut to 72 bytes: two passwords that differ only
    after byte 72 are never treated as the same.
    """
    if too_long_for_bcrypt(plain):
        return False
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode())


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
