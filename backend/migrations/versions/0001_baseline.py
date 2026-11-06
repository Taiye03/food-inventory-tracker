"""Baseline: the schema as of commit b8c135a (P0-5 step 2).

Creates the four tables exactly as app/models.py defines them, with the
naming convention from app/database.py. tests/test_migrations.py checks that
the result is identical to what Base.metadata.create_all() produces.

FRESH databases get this by running `alembic upgrade head`.

EXISTING databases (created by the app's create_all() before migrations) must
NOT be upgraded through this revision: their tables already exist. They are
backed up, checked with scripts/db_preflight.py, and then *stamped* as 0001
(see docs/DATABASE_RECOVERY.md). The guard below refuses to run on them.

Revision ID: 0001
Revises:
Create Date: 2026-11-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ("users", "products", "orders", "order_allocations")


class ExistingDatabaseError(RuntimeError):
    pass


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names()) & set(TABLES)
    if existing:
        raise ExistingDatabaseError(
            f"This database already has FoodHeroes tables ({', '.join(sorted(existing))}). "
            "The baseline only creates a NEW database. For an existing one: back it up, run "
            "scripts/db_preflight.py, then `alembic stamp 0001` (see docs/DATABASE_RECOVERY.md). "
            "Nothing has been changed."
        )

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=50), nullable=False),
        sa.Column("fullname", sa.String(length=100), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("email", sa.String(length=120), nullable=False),
        sa.Column("password_hash", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    op.create_index(op.f("ix_users_id"), "users", ["id"], unique=False)
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)

    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.String(length=30), nullable=False),
        sa.Column("product_name", sa.String(length=100), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("date_added", sa.Date(), nullable=False),
        sa.Column("expiry_date", sa.Date(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_products")),
    )
    op.create_index(op.f("ix_products_batch_id"), "products", ["batch_id"], unique=True)
    op.create_index(op.f("ix_products_id"), "products", ["id"], unique=False)

    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_date", sa.Date(), nullable=False),
        sa.Column("customer_name", sa.String(length=100), nullable=False),
        sa.Column("product_name", sa.String(length=100), nullable=False),
        sa.Column("cartons", sa.Float(), nullable=True),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("amount_paid", sa.Float(), nullable=False),
        sa.Column("notes", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orders")),
    )
    op.create_index(op.f("ix_orders_customer_name"), "orders", ["customer_name"], unique=False)
    op.create_index(op.f("ix_orders_id"), "orders", ["id"], unique=False)
    op.create_index(op.f("ix_orders_order_date"), "orders", ["order_date"], unique=False)

    op.create_table(
        "order_allocations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=True),
        sa.Column("batch_id", sa.String(length=30), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], name=op.f("fk_order_allocations_order_id_orders")),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], name=op.f("fk_order_allocations_product_id_products")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_allocations")),
    )
    op.create_index(op.f("ix_order_allocations_order_id"), "order_allocations", ["order_id"], unique=False)


def downgrade() -> None:
    # Only ever used on fresh/test databases. To undo a migration on real data,
    # restore the verified backup instead (docs/DATABASE_RECOVERY.md).
    op.drop_index(op.f("ix_order_allocations_order_id"), table_name="order_allocations")
    op.drop_table("order_allocations")
    op.drop_index(op.f("ix_orders_order_date"), table_name="orders")
    op.drop_index(op.f("ix_orders_id"), table_name="orders")
    op.drop_index(op.f("ix_orders_customer_name"), table_name="orders")
    op.drop_table("orders")
    op.drop_index(op.f("ix_products_id"), table_name="products")
    op.drop_index(op.f("ix_products_batch_id"), table_name="products")
    op.drop_table("products")
    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.drop_index(op.f("ix_users_id"), table_name="users")
    op.drop_table("users")
