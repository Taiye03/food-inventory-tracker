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
  - editing an order changes stock only if its product or cartons change,
    and keeps the batches it was originally sold from (see adjust_stock)
  - deleting an order puts its stock back in the batches it came from
  - orders imported from the old ledger never touched stock, so deleting
    them doesn't add stock either
  - a sale with no carton count is recorded without changing stock

Corrections (decided Nov 2026):
  - reducing a sale's cartons returns the difference to the batch allocated
    MOST RECENTLY first. Sales take the soonest-expiring batch first, so the
    cartons from that batch are the ones most certainly sold. Returned cartons
    stay with their original batch even if it has expired, so they never
    become sellable again.
  - increasing a sale keeps its original allocations and takes only the extra
    from today's sellable stock.

Known limitation (future design decision, revisit with the stock movement
ledger): an order that never took stock (an imported ledger order, or a sale
saved with blank cartons) never takes stock when edited either, even if a
carton count is added later. This protects imported orders from removing
today's stock, but means a blank-carton sale corrected to "5" leaves stock
unchanged. Decide whether such edits should take stock, and how to tell
imported orders apart, when designing the movement ledger.
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_admin
from app.database import get_db
from app.inventory import sellable
from app.models import Order, OrderAllocation, Product, User
from app.schemas import OrderCreate, OrderOut, OrderUpdate, StockTaken

router = APIRouter(prefix="/orders", tags=["orders"])


def get_or_404(db: Session, order_id: int) -> Order:
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


def take_stock(db: Session, order: Order, quantity: Optional[float] = None) -> None:
    """Take cartons out of the product's sellable batches, soonest expiry first.

    By default takes all of order.cartons (a new sale). Pass `quantity` to take
    only that many more (a sale corrected upwards).
    """
    needed = order.cartons if quantity is None else quantity
    if not needed:
        return
    # Send any earlier changes in this transaction (e.g. returned stock) to the
    # database first, because the query below filters on quantity in SQL.
    db.flush()
    batches = (
        db.query(Product)
        .filter(Product.product_name == order.product_name)
        .filter(sellable())  # never sell expired stock
        .filter(Product.quantity > 0)
        .order_by(Product.expiry_date, Product.batch_id)
        .all()
    )
    available = sum(b.quantity for b in batches)
    if available < needed:
        if not db.query(Product).filter(Product.product_name == order.product_name).first():
            raise HTTPException(400, f"There's no product called '{order.product_name}' in stock.")
        wanted = f"This sale needs {needed:g}." if quantity is None else f"This change needs {needed:g} more."
        raise HTTPException(
            400,
            f"Only {available:g} of '{order.product_name}' in stock "
            f"(not counting expired batches). {wanted}",
        )
    for batch in batches:
        if needed <= 0:
            break
        take = min(batch.quantity, needed)
        batch.quantity -= take
        needed -= take
        # One allocation row per batch: top up the existing one if there is one.
        alloc = db.query(OrderAllocation).filter_by(order_id=order.id, product_id=batch.id).first()
        if alloc:
            alloc.quantity += take
        else:
            db.add(OrderAllocation(order_id=order.id, product_id=batch.id,
                                   batch_id=batch.batch_id, quantity=take))


def return_stock(db: Session, order: Order, quantity: Optional[float] = None) -> None:
    """Put cartons back in the batches they came from.

    By default returns everything (order deleted, or product changed). Pass
    `quantity` to return only that many (a sale corrected downwards), starting
    with the batch taken from last. Returned cartons go to their original batch
    even if it has expired since, so they don't become sellable.
    """
    remaining = quantity
    allocs = db.query(OrderAllocation).filter_by(order_id=order.id).order_by(OrderAllocation.id.desc())
    for alloc in allocs.all():
        if remaining is not None and remaining <= 0:
            break
        give = alloc.quantity if remaining is None else min(alloc.quantity, remaining)
        batch = db.get(Product, alloc.product_id) if alloc.product_id else None
        if batch:  # the batch may have been deleted since; then there's nothing to return to
            batch.quantity += give
        alloc.quantity -= give
        if alloc.quantity <= 0:
            db.delete(alloc)
        if remaining is not None:
            remaining -= give


def adjust_stock(db: Session, order: Order, old_product: str, old_cartons: Optional[float]) -> None:
    """After an edit, change stock by the difference only, so the sale keeps the
    batches it was really sold from.

      same product and cartons -> nothing changes
      fewer cartons            -> the difference goes back to the original batches
      more cartons             -> only the extra is taken, from sellable stock
      different product        -> different goods: return all, then take the new product
    """
    old, new = old_cartons or 0, order.cartons or 0
    if order.product_name != old_product:
        return_stock(db, order)
        take_stock(db, order)
    elif new < old:
        return_stock(db, order, old - new)
    elif new > old:
        take_stock(db, order, new - old)


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
    old_product, old_cartons = order.product_name, order.cartons
    for field, value in data.model_dump().items():
        setattr(order, field, value)
    # Only orders that took stock in the first place have stock to adjust, so
    # editing an old imported ledger order (e.g. to mark it paid) never
    # suddenly removes cartons from today's stock.
    if had_stock:
        try:
            adjust_stock(db, order, old_product, old_cartons)
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
