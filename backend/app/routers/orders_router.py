"""
Orders: the client's sales ledger (date, customer, cartons, amount, paid).
New feature — the Tkinter app had no orders at all (task #2).

Who can do what:
  - anyone logged in can view orders and record a new one (staff make sales)
  - only admins can edit or delete an order

Stock (decided Oct 2026):
  - recording a sale takes the cartons out of stock (1 carton = 1 unit)
  - stock is taken from the batch that expires SOONEST first ("first expired,
    first out"), and expired batches are never sold
  - if there isn't enough stock, the sale is blocked with a clear message
  - editing or deleting an order puts its stock back first
  - orders imported from the old ledger never touched stock, so deleting
    them doesn't add stock either
  - a sale with no carton count is recorded without changing stock
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_admin
from app.database import get_db
from app.models import Order, OrderAllocation, Product, User
from app.schemas import OrderCreate, OrderOut, OrderUpdate, StockTaken

router = APIRouter(prefix="/orders", tags=["orders"])


def get_or_404(db: Session, order_id: int) -> Order:
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


def take_stock(db: Session, order: Order) -> None:
    """Take order.cartons out of the product's batches, soonest expiry first."""
    if not order.cartons:
        return
    batches = (
        db.query(Product)
        .filter(Product.product_name == order.product_name)
        .filter(Product.expiry_date >= date.today())  # never sell expired stock
        .filter(Product.quantity > 0)
        .order_by(Product.expiry_date, Product.batch_id)
        .all()
    )
    available = sum(b.quantity for b in batches)
    if available < order.cartons:
        if not db.query(Product).filter(Product.product_name == order.product_name).first():
            raise HTTPException(400, f"There's no product called '{order.product_name}' in stock.")
        raise HTTPException(
            400,
            f"Only {available:g} of '{order.product_name}' in stock "
            f"(not counting expired batches). This sale needs {order.cartons:g}.",
        )
    needed = order.cartons
    for batch in batches:
        if needed <= 0:
            break
        take = min(batch.quantity, needed)
        batch.quantity -= take
        needed -= take
        db.add(OrderAllocation(order_id=order.id, product_id=batch.id,
                               batch_id=batch.batch_id, quantity=take))


def return_stock(db: Session, order: Order) -> None:
    """Undo take_stock: put every carton back in the batch it came from."""
    for alloc in db.query(OrderAllocation).filter_by(order_id=order.id).all():
        batch = db.get(Product, alloc.product_id) if alloc.product_id else None
        if batch:  # the batch may have been deleted since; then there's nothing to return to
            batch.quantity += alloc.quantity
        db.delete(alloc)


def to_out(db: Session, order: Order) -> OrderOut:
    out = OrderOut.model_validate(order)
    out.stock_taken = [
        StockTaken(batch_id=a.batch_id, quantity=a.quantity)
        for a in db.query(OrderAllocation).filter_by(order_id=order.id).order_by(OrderAllocation.id)
    ]
    return out


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
    return [to_out(db, o) for o in q.order_by(Order.order_date.desc(), Order.id.desc()).all()]


@router.post("", response_model=OrderOut, status_code=201)
def create_order(data: OrderCreate, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    order = Order(**data.model_dump())
    db.add(order)
    db.flush()  # gives the order an id without committing yet
    try:
        take_stock(db, order)
    except HTTPException:
        db.rollback()  # not enough stock: nothing is saved
        raise
    db.commit()
    db.refresh(order)
    return to_out(db, order)


@router.put("/{order_id}", response_model=OrderOut)
def update_order(order_id: int, data: OrderUpdate, db: Session = Depends(get_db),
                 _: User = Depends(require_admin)):
    order = get_or_404(db, order_id)
    had_stock = db.query(OrderAllocation).filter_by(order_id=order.id).first() is not None
    return_stock(db, order)
    for field, value in data.model_dump().items():
        setattr(order, field, value)
    # Only re-take stock for orders that took stock in the first place, so
    # editing an old imported ledger order (e.g. to mark it paid) never
    # suddenly removes cartons from today's stock.
    if had_stock:
        try:
            take_stock(db, order)
        except HTTPException:
            db.rollback()
            raise
    db.commit()
    db.refresh(order)
    return to_out(db, order)


@router.delete("/{order_id}", status_code=204)
def delete_order(order_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    order = get_or_404(db, order_id)
    return_stock(db, order)
    db.delete(order)
    db.commit()
