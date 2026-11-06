"""Legacy column types: bring older SQLite databases in line with the models (P0-5 step 3).

Databases created by the app before migrations existed keep the column types
of whichever version first created them, because create_all() never alters a
column. Two such differences are known:

  products.quantity   INTEGER NOT NULL           (before commit a689ed9)  -> FLOAT NOT NULL
  orders.cartons      INTEGER NOT NULL           (before commit d27f08e)  -> FLOAT, nullable

This revision looks at the ACTUAL columns and rebuilds a table only if it has
one of these differences. Every other table is left untouched, and on a fresh
database (created by 0001) it does nothing at all.

A rebuild uses Alembic's SQLite batch mode: create the new table, copy every
row, drop the old table, rename the new one into place, recreate its indexes.
It runs inside env.py's safety net (explicit BEGIN, foreign keys off, then
PRAGMA foreign_key_check before COMMIT), so a failure leaves nothing changed.

Values: half cartons (37.5) are kept exactly. Whole numbers stored in the old
INTEGER column (e.g. 20) become 20.0: numerically identical.

Only SQLite needs this. PostgreSQL databases are always created fresh by 0001.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.database import NAMING_CONVENTION

revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column(table: str, name: str) -> dict:
    return next(c for c in sa.inspect(op.get_bind()).get_columns(table) if c["name"] == name)


def _is_float(col: dict) -> bool:
    return col["type"].__visit_name__.upper() in ("FLOAT", "REAL")


def needs_quantity_fix() -> bool:
    return not _is_float(_column("products", "quantity"))


def needs_cartons_fix() -> bool:
    col = _column("orders", "cartons")
    return not _is_float(col) or not col["nullable"]


def upgrade() -> None:
    if op.get_bind().dialect.name != "sqlite":
        return

    if needs_quantity_fix():
        with op.batch_alter_table("products", recreate="always", naming_convention=NAMING_CONVENTION) as batch:
            batch.alter_column("quantity", existing_type=sa.Integer(), type_=sa.Float(), existing_nullable=False)

    if needs_cartons_fix():
        with op.batch_alter_table("orders", recreate="always", naming_convention=NAMING_CONVENTION) as batch:
            batch.alter_column("cartons", existing_type=sa.Integer(), type_=sa.Float(),
                               existing_nullable=False, nullable=True)


def downgrade() -> None:
    # Deliberately does nothing. Going back to INTEGER would round or reject
    # half cartons (37.5), so this change isn't reversed. To undo it on a real
    # database, restore the verified backup (docs/DATABASE_RECOVERY.md).
    pass
