"""
Replaces productspage.py. Batch ID format (BATCH-YYYYMMDD-XXX), date checks
and the 3-day expiry warning all behave the same as the Tkinter version.

Anyone logged in can view products and alerts; only admins can change them.
"""
import csv
import io
import os
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_admin
from app.database import get_db
from app.models import Product, User
from app.schemas import ExpiryAlert, LowStockAlert, ProductCreate, ProductOut, ProductUpdate

router = APIRouter(prefix="/products", tags=["products"])

EXPIRY_WARNING_DAYS = 3
# Warn when a product's total quantity (all batches) is at or below this.
# Change it without touching code: export LOW_STOCK_THRESHOLD=20
LOW_STOCK_THRESHOLD = int(os.getenv("LOW_STOCK_THRESHOLD", "10"))


def generate_batch_id(db: Session) -> str:
    """Same as the old generate_batch_id(), but reads the database not the CSV."""
    prefix = f"BATCH-{date.today():%Y%m%d}-"
    todays = db.query(Product.batch_id).filter(Product.batch_id.like(f"{prefix}%")).all()
    numbers = [int(b[0].rsplit("-", 1)[-1]) for b in todays]
    return f"{prefix}{max(numbers, default=0) + 1:03d}"


def get_or_404(db: Session, batch_id: str) -> Product:
    product = db.query(Product).filter(Product.batch_id == batch_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Batch ID not found")
    return product


@router.get("", response_model=list[ProductOut])
def list_products(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.query(Product).order_by(Product.batch_id).all()


# Declared before "/{batch_id}" so "alerts" and "export" aren't read as batch IDs.
@router.get("/alerts", response_model=list[ExpiryAlert])
def expiry_alerts(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    """Replaces notify_expiry()."""
    alerts = []
    for p in db.query(Product).order_by(Product.expiry_date).all():
        days_left = (p.expiry_date - date.today()).days
        if days_left < 0:
            alerts.append(ExpiryAlert(
                batch_id=p.batch_id, product_name=p.product_name, expiry_date=p.expiry_date,
                days_left=days_left, status="expired",
                message=f"Product '{p.product_name}' has expired!",
            ))
        elif days_left <= EXPIRY_WARNING_DAYS:
            alerts.append(ExpiryAlert(
                batch_id=p.batch_id, product_name=p.product_name, expiry_date=p.expiry_date,
                days_left=days_left, status="expiring_soon",
                message=f"Product '{p.product_name}' is nearing expiry in {days_left} days!",
            ))
    return alerts


@router.get("/low-stock", response_model=list[LowStockAlert])
def low_stock_alerts(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    """New: the old app had expiry alerts but no low-stock alerts (task #4).
    Adds up every batch of the same product, so 3 batches of 4 crates = 12."""
    totals = (
        db.query(Product.product_name, func.sum(Product.quantity))
        .group_by(Product.product_name)
        .order_by(Product.product_name)
        .all()
    )
    return [
        LowStockAlert(
            product_name=name,
            quantity=total,
            threshold=LOW_STOCK_THRESHOLD,
            message=f"Low stock: only {total} of '{name}' left.",
        )
        for name, total in totals
        if total <= LOW_STOCK_THRESHOLD
    ]


@router.get("/export")
def export_csv(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    """Replaces export_to_excel(): downloads the products as a CSV file."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Batch ID", "Product Name", "Price", "Quantity", "Date Added", "Expiry Date"])
    for p in db.query(Product).order_by(Product.batch_id).all():
        writer.writerow([p.batch_id, p.product_name, p.price, p.quantity, p.date_added, p.expiry_date])
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=products_export.csv"},
    )


@router.get("/{batch_id}", response_model=ProductOut)
def get_product(batch_id: str, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return get_or_404(db, batch_id)


@router.post("", response_model=ProductOut, status_code=201)
def save_product(data: ProductCreate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    product = Product(batch_id=generate_batch_id(db), **data.model_dump())
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


@router.put("/{batch_id}", response_model=ProductOut)
def update_product(batch_id: str, data: ProductUpdate, db: Session = Depends(get_db),
                   _: User = Depends(require_admin)):
    product = get_or_404(db, batch_id)
    for field, value in data.model_dump().items():
        setattr(product, field, value)
    db.commit()
    db.refresh(product)
    return product


@router.delete("/{batch_id}", status_code=204)
def delete_product(batch_id: str, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    db.delete(get_or_404(db, batch_id))
    db.commit()
