"""
Database tables. Each class here replaces one of the old CSV files.
"""
from sqlalchemy import Column, Date, Float, Integer, String

from app.database import Base


class User(Base):
    """Replaces users.csv. Stores a bcrypt hash, never the plain password."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    fullname = Column(String(100), nullable=False)
    role = Column(String(20), nullable=False, default="User")  # "Admin" or "User"
    email = Column(String(120), nullable=False)
    password_hash = Column(String(128), nullable=False)


class Product(Base):
    """Replaces products.csv (same columns)."""

    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(String(30), unique=True, index=True, nullable=False)
    product_name = Column(String(100), nullable=False)
    price = Column(Float, nullable=False)
    quantity = Column(Integer, nullable=False)
    date_added = Column(Date, nullable=False)
    expiry_date = Column(Date, nullable=False)


class Order(Base):
    """A sale, one line from the handwritten ledger. New table — created
    automatically on startup, so existing products/users are untouched."""

    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    order_date = Column(Date, nullable=False, index=True)
    customer_name = Column(String(100), nullable=False, index=True)
    product_name = Column(String(100), nullable=False)
    cartons = Column(Integer, nullable=False)
    amount = Column(Float, nullable=False)
    amount_paid = Column(Float, nullable=False, default=0)
    notes = Column(String(255), nullable=True)
