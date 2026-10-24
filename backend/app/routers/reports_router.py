"""
Reports: summaries of the Orders table, worked out by the database
with GROUP BY instead of looping in Python.

Admins only, since these show the business's full sales and debts.
"""
from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db
from app.models import Order, User
from app.schemas import CustomerReport, MonthReport

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/monthly", response_model=list[MonthReport])
def sales_by_month(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    """One row per month: number of orders, cartons, sales and amount still owed."""
    # strftime works on SQLite; on MySQL this would be DATE_FORMAT(order_date, '%Y-%m')
    month = func.strftime("%Y-%m", Order.order_date).label("month")
    rows = (
        db.query(
            month,
            func.count(Order.id),
            func.coalesce(func.sum(Order.cartons), 0),
            func.sum(Order.amount),
            func.sum(Order.amount - Order.amount_paid),
        )
        .group_by(month)
        .order_by(month)
        .all()
    )
    return [
        MonthReport(month=m, orders=n, cartons=c, sales=s, owed=o)
        for m, n, c, s, o in rows
    ]


@router.get("/customers", response_model=list[CustomerReport])
def sales_by_customer(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    """One row per customer, biggest buyer first."""
    rows = (
        db.query(
            Order.customer_name,
            func.count(Order.id),
            func.coalesce(func.sum(Order.cartons), 0),
            func.sum(Order.amount),
            func.sum(Order.amount - Order.amount_paid),
            func.max(Order.order_date),
        )
        .group_by(Order.customer_name)
        .order_by(func.sum(Order.amount).desc())
        .all()
    )
    return [
        CustomerReport(customer_name=name, orders=n, cartons=c, sales=s, owed=o, last_order=last)
        for name, n, c, s, o, last in rows
    ]
