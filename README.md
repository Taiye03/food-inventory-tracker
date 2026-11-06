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
```

The app never creates or changes database tables itself; migrations do. It refuses to start until the database is at the latest migration, and the error message says what to run.

**New installation** (no database yet):

```bash
export DATABASE_URL=sqlite:///$PWD/foodheroes.db
export SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"   # or your saved key
alembic upgrade head              # creates the tables
python -m scripts.create_admin --database "$PWD/foodheroes.db" \
    --username owner --fullname "Business Owner" --email owner@example.com
uvicorn app.main:app --reload
```

There is no built-in account and no default password. `create_admin` asks for the new administrator's password twice (hidden as you type). The backend refuses to start until the database has an administrator. See "Creating the first administrator" and "Password rules" (12 characters to 72 bytes, the same everywhere) in [docs/DATABASE_RECOVERY.md](docs/DATABASE_RECOVERY.md).

The backend won't start without its own `SECRET_KEY` (at least 32 random characters), which signs every login. There is no built-in default. Keep the same key between restarts, or everyone is logged out; see "Ending existing logins" in the same document.

**Existing database** (created by an older version, before migrations): don't run `alembic upgrade head` on it straight away. Follow "Bringing an existing database under migrations" in [docs/DATABASE_RECOVERY.md](docs/DATABASE_RECOVERY.md): preflight, backup, `alembic stamp 0001`, then `alembic upgrade head`.

**After pulling new code**, if startup says the database is outdated: stop the app, back up, run `alembic upgrade head`.

Open http://localhost:8000/docs to try every endpoint in the browser.

Run the tests with `pytest -q` from `backend/`. They use a throwaway `test_foodheroes.db` and never touch the database in `DATABASE_URL`.

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
| `init_users_csv()` broken on fresh install | The first admin is created with `scripts/create_admin.py`; there is no default password |
| Update/delete accepted any Batch ID, skipped date checks | 404 for unknown Batch IDs; expiry date is checked on every save |
| CSV files | SQLAlchemy database (SQLite locally, MySQL by setting `DATABASE_URL`) |
| One computer at a time | Anyone with a browser and a login |

Batch IDs (`BATCH-YYYYMMDD-XXX`), the 3-day expiry warning and CSV export behave the same as before.

## New features (not in the desktop app)

- **Low-stock alerts:** warns when a product's total quantity across all batches is at or below 10. Change the level with `export LOW_STOCK_THRESHOLD=20` before starting the backend.
- **Orders:** the client's sales ledger on the web: date, customer, product, cartons, amount and amount paid. The backend works out each order's balance and status (Paid / Part paid / Owing). The page shows totals and can filter by customer or unpaid orders. Any logged-in user can record a sale; only admins can edit or delete one.
- **Sales take stock:** recording a sale takes the cartons out of stock, from the batch that expires soonest first, and never from expired batches. A sale bigger than the stock is blocked. Editing or deleting an order puts the stock back. Orders imported from the old ledger never touched stock.
- **Reports (admins):** sales by month and by customer, with what each still owes.

## Still to do

- Confirm unclear ledger rows and payment statuses (marked 📝 on the Orders page)
- Demand-forecasting model
