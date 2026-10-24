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
