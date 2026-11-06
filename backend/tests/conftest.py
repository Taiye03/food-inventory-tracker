"""
Shared test setup.

pytest loads this file before any test module, so these are fixed before the
app is imported. Even if your shell has DATABASE_URL or SECRET_KEY set to the
real database or your real signing key, the tests never use them.
"""
import os
import secrets

os.environ["DATABASE_URL"] = "sqlite:///./test_foodheroes.db"
# A fresh random signing key for every test run: tests sign real tokens with
# a valid key, without any built-in default and without your private key.
os.environ["SECRET_KEY"] = secrets.token_urlsafe(48)
for name in ("TOKEN_MINUTES", "FRONTEND_ORIGINS"):
    os.environ.pop(name, None)   # tests start from the documented defaults
