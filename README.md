# FoodHeroes Stock Management

Stock management system for a wholesale egg distributor, moving from a Tkinter desktop app to a website so several people in different locations can use it at once.

```
backend/    FastAPI + SQLAlchemy API (login, users, products, expiry alerts)
frontend/   React + Vite website (in progress)
*.py        Original Tkinter desktop app (loginpage.py, productspage.py, users.py), kept for reference
```

## Run the backend

```bash
cd backend
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open http://localhost:8000/docs to try every endpoint in the browser.

On first run it creates `foodheroes.db` and one account:

- username: `admin`
- password: `changeme123` (change it)

Run the tests with `pytest -q` from `backend/`.

## Run the frontend

Needs Node.js (`node -v` should print a version).

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173.

## What the website version fixes

| Old desktop app | Website version |
|---|---|
| Passwords stored as plain text in users.csv | bcrypt hashes; the API never returns passwords |
| Roles defined but not enforced | "User" accounts can view; only "Admin" can change data (403 otherwise) |
| `init_users_csv()` broken on fresh install | A first admin account is created automatically |
| Update/delete accepted any Batch ID, skipped date checks | 404 for unknown Batch IDs; expiry date is checked on every save |
| CSV files | SQLAlchemy database (SQLite locally, MySQL by setting `DATABASE_URL`) |
| One computer at a time | Anyone with a browser and a login |

Batch IDs (`BATCH-YYYYMMDD-XXX`), the 3-day expiry warning and CSV export behave the same as before.

## New features (not in the desktop app)

- **Low-stock alerts:** warns when a product's total quantity across all batches is at or below 10. Change the level with `export LOW_STOCK_THRESHOLD=20` before starting the backend.
- **Orders:** the client's sales ledger on the web: date, customer, product, cartons, amount and amount paid. The backend works out each order's balance and status (Paid / Part paid / Owing). The page shows totals and can filter by customer or unpaid orders. Any logged-in user can record a sale; only admins can edit or delete one.

## Still to do

- Import the transcribed ledger data into Orders
- Decide whether orders should reduce product stock automatically
- Real reporting
- Demand-forecasting model
