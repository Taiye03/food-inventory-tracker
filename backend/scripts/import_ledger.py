"""
Import the client's transcribed sales ledger into the Orders table.

Run from the backend/ folder (with the venv active):

    python -m scripts.import_ledger data/sales_ledger.csv

Safe to run more than once: a row that's already in the database
(same date, customer and amount) is skipped, not added twice.

Decisions agreed with the client data owner (Taiye, Oct 2026):
  - blank Status        -> not paid yet (amount_paid = 0, shows as Owing)
  - "Paid"              -> amount_paid = amount
  - "Pharm"             -> "Pharmcare" (same customer)
  - "ACSK Zenith"       -> "ACSK" (same customer)
  - unclear rows        -> imported with their notes, so they can be fixed later
  - product             -> "Eggs (carton)" for every row (ledger has no product column)
"""
import csv
import sys
from datetime import date

from app.database import Base, SessionLocal, engine
from app.main import upgrade_orders_table
from app.models import Order

PRODUCT = "Eggs (carton)"
SAME_CUSTOMER = {"Pharm": "Pharmcare", "ACSK Zenith": "ACSK"}


def clean_customer(raw: str, notes: str) -> tuple[str, str]:
    name = raw.strip()
    if name in ("", "?"):
        return "Unknown", notes
    if name in SAME_CUSTOMER:
        merged = SAME_CUSTOMER[name]
        extra = f"Written as '{name}' in ledger"
        return merged, f"{notes}; {extra}" if notes else extra
    return name, notes


def read_rows(path: str):
    # utf-8-sig handles files saved from Excel (they start with a hidden marker)
    with open(path, newline="", encoding="utf-8-sig") as f:
        for line_no, row in enumerate(csv.DictReader(f), start=2):
            notes = (row.get("Notes") or "").strip()
            customer, notes = clean_customer(row["Customer"], notes)
            amount = float(row["Amount (₦)"])
            cartons_raw = (row.get("Cartons (ctn)") or "").strip()
            paid = (row.get("Status") or "").strip().lower() == "paid"
            yield line_no, Order(
                order_date=date.fromisoformat(row["Date"].strip()),
                customer_name=customer,
                product_name=PRODUCT,
                cartons=float(cartons_raw) if cartons_raw else None,
                amount=amount,
                amount_paid=amount if paid else 0,
                notes=notes or None,
            )


def main(path: str) -> None:
    upgrade_orders_table()
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    added = skipped = 0
    try:
        for line_no, order in read_rows(path):
            exists = db.query(Order).filter_by(
                order_date=order.order_date,
                customer_name=order.customer_name,
                amount=order.amount,
            ).first()
            if exists:
                skipped += 1
                continue
            db.add(order)
            added += 1
            flag = f"  (note: {order.notes})" if order.notes else ""
            print(f"  line {line_no}: {order.order_date} {order.customer_name} ₦{order.amount:,.0f}{flag}")
        db.commit()
    finally:
        db.close()
    print(f"\nDone: {added} added, {skipped} already there.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python -m scripts.import_ledger path/to/sales_ledger.csv")
    main(sys.argv[1])
