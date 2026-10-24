"""
Orders: the client's sales ledger (date, customer, cartons, amount, paid).
New feature — the Tkinter app had no orders at all (task #2).

Who can do what:
  - anyone logged in can view orders and record a new one (staff make sales)
  - only admins can edit or delete an order
For now orders don't change product stock; that can be linked later.
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_admin
from app.database import get_db
from app.models import Order, User
from app.schemas import OrderCreate, OrderOut, OrderUpdate

router = APIRouter(prefix="/orders", tags=["orders"])


def get_or_404(db: Session, order_id: int) -> Order:
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@router.get("", response_model=list[OrderOut])
def list_orders(
    customer: Optional[str] = None,
    start: Optional[date] = None,
    end: Optional[date] = None,
    unpaid_only: bool = False,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Newest first. Optional filters: ?customer=Ade&start=2026-10-01&unpaid_only=true"""
    q = db.query(Order)
    if customer:
        q = q.filter(Order.customer_name.ilike(f"%{customer}%"))
    if start:
        q = q.filter(Order.order_date >= start)
    if end:
        q = q.filter(Order.order_date <= end)
    if unpaid_only:
        q = q.filter(Order.amount_paid < Order.amount)
    return q.order_by(Order.order_date.desc(), Order.id.desc()).all()


@router.post("", response_model=OrderOut, status_code=201)
def create_order(data: OrderCreate, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    order = Order(**data.model_dump())
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


@router.put("/{order_id}", response_model=OrderOut)
def update_order(order_id: int, data: OrderUpdate, db: Session = Depends(get_db),
                 _: User = Depends(require_admin)):
    order = get_or_404(db, order_id)
    for field, value in data.model_dump().items():
        setattr(order, field, value)
    db.commit()
    db.refresh(order)
    return order


@router.delete("/{order_id}", status_code=204)
def delete_order(order_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    db.delete(get_or_404(db, order_id))
    db.commit()
