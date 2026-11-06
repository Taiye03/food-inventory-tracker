"""
Shared test setup.

pytest loads this file before any test module, so DATABASE_URL is fixed to the
throwaway test database before app.database creates its engine. Even if your
shell has DATABASE_URL pointing at a real database, the tests never touch it.
"""
import os

os.environ["DATABASE_URL"] = "sqlite:///./test_foodheroes.db"
