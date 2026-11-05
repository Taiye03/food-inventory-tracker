"""
Inventory rules shared by more than one part of the app.

The one rule here so far: what counts as SELLABLE stock. Sales and low-stock
alerts must agree on it, so it's defined once instead of being written out in
each router (P0-2: the low-stock alert used to count expired batches).
"""
from datetime import date

from sqlalchemy import case

from app.models import Product


def sellable():
    """SQL condition: this batch can still be sold.

    A batch expiring today is still sellable; from tomorrow it isn't.
    A function, not a constant, so "today" is worked out on every request.
    """
    return Product.expiry_date >= date.today()


def sellable_quantity():
    """SQL expression: the batch's quantity if it's sellable, otherwise 0.

    Use inside func.sum(...) so a product whose batches have ALL expired still
    gets a row (with 0), instead of disappearing like it would with a WHERE filter.
    """
    return case((sellable(), Product.quantity), else_=0)
