"""
Read-only health check of a FoodHeroes SQLite database, before migrations.

Run from backend/:

    python -m scripts.db_preflight                         # uses DATABASE_URL (required; no default)
    python -m scripts.db_preflight path/to/foodheroes.db
    python -m scripts.db_preflight path/to/foodheroes.db --json

It compares the database with the models in app/models.py and reports:
  - missing tables and unknown tables (e.g. a leftover orders_old)
  - columns that are missing, unexpected, or have a different type or NULL rule
    ("known legacy" differences, which a planned migration fixes, are listed
    separately from unexpected ones)
  - missing and unknown indexes, missing and unknown foreign keys
  - orphaned rows: values pointing at a row that doesn't exist, listed row by
    row so each one can be investigated. Nothing is fixed automatically.
  - PRAGMA integrity_check, row counts, and the migration revision if any

Exit code 0 means no issues (known legacy differences are not issues);
1 means issues were found; 2 means the database couldn't be read.

The database is opened read-only and is never modified.
"""
import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.dialects import sqlite as sqlite_dialect

from scripts.db_common import (
    DatabaseToolError,
    connect_readonly,
    default_db_path,
    file_sha256,
    integrity,
    quote_ident,
    row_counts,
    user_tables,
)

# Tables that may exist without being in the models.
ALLOWED_EXTRA_TABLES = {"alembic_version"}

# Differences that older databases are known to have, because create_all()
# never alters existing columns. Migration 0002 (P0-5 step 3) will fix them.
KNOWN_LEGACY = {
    ("products", "quantity", "type"): "products.quantity was INTEGER before commit a689ed9",
    ("orders", "cartons", "type"): "orders.cartons was INTEGER before commit d27f08e",
    ("orders", "cartons", "nullable"): "orders.cartons was NOT NULL before commit d27f08e",
}

HINTS = {
    "orders_old": "left behind by an interrupted upgrade_orders_table(); compare it with orders before doing anything",
}


@dataclass
class Report:
    database: str
    checked_at: str
    file_size: int
    file_sha256: str
    integrity: str = ""
    revision: str | None = None
    row_counts: dict = field(default_factory=dict)
    summary: dict = field(default_factory=dict)
    issues: list = field(default_factory=list)
    known_legacy: list = field(default_factory=list)
    orphans: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def issue(self, kind: str, message: str):
        self.issues.append({"kind": kind, "message": message})


def _expected():
    """The schema the app's models describe."""
    import app.models  # noqa: F401  registers the tables on Base.metadata
    from app.database import Base
    return Base.metadata


def _norm_type(t: str) -> str:
    return "".join(t.upper().split())


def _check_columns(conn, report, table):
    dialect = sqlite_dialect.dialect()
    found = {r[1]: {"type": r[2], "notnull": bool(r[3]), "pk": bool(r[5])}
             for r in conn.execute(f"PRAGMA table_info({quote_ident(table.name)})")}
    for col in table.columns:
        name = f"{table.name}.{col.name}"
        if col.name not in found:
            report.issue("missing_column", f"{name} is missing")
            continue
        db = found[col.name]
        want_type = _norm_type(col.type.compile(dialect=dialect))
        if _norm_type(db["type"]) != want_type:
            _drift(report, table.name, col.name, "type", db["type"], want_type)
        want_notnull = not col.nullable
        if db["notnull"] != want_notnull and not (col.primary_key and db["pk"]):
            _drift(report, table.name, col.name, "nullable",
                   "NOT NULL" if db["notnull"] else "NULL", "NOT NULL" if want_notnull else "NULL")
    for extra in sorted(set(found) - {c.name for c in table.columns}):
        report.issue("unknown_column", f"{table.name}.{extra} is not in the models")


def _drift(report, table, column, what, found, expected):
    entry = {"table": table, "column": column, "difference": what, "found": found, "expected": expected}
    reason = KNOWN_LEGACY.get((table, column, what))
    if reason:
        report.known_legacy.append({**entry, "reason": reason})
    else:
        report.issue(f"column_{what}", f"{table}.{column}: {what} is {found}, models expect {expected}")


def _check_indexes(conn, report, table):
    expected = {ix.name: (tuple(c.name for c in ix.columns), bool(ix.unique)) for ix in table.indexes}
    found = {}
    for _seq, name, unique, origin, _partial in conn.execute(f"PRAGMA index_list({quote_ident(table.name)})"):
        if origin != "c":  # automatic indexes for PRIMARY KEY / UNIQUE constraints
            continue
        cols = tuple(r[2] for r in conn.execute(f"PRAGMA index_info({quote_ident(name)})"))
        found[name] = (cols, bool(unique))
    for name, (cols, unique) in sorted(expected.items()):
        if name not in found:
            report.issue("missing_index", f"index {name} on {table.name}{cols} is missing")
        elif found[name] != (cols, unique):
            report.issue("index_differs", f"index {name}: found {found[name]}, models expect {(cols, unique)}")
    for name in sorted(set(found) - set(expected)):
        report.issue("unknown_index", f"index {name} on {table.name}{found[name][0]} is not in the models")


def _expected_fks(table):
    return {(fk.parent.name, fk.column.table.name, fk.column.name) for fk in table.foreign_keys}


def _check_foreign_keys(conn, report, table):
    found = {(r[3], r[2], r[4]) for r in conn.execute(f"PRAGMA foreign_key_list({quote_ident(table.name)})")}
    expected = _expected_fks(table)
    for col, ref_t, ref_c in sorted(expected - found):
        report.issue("missing_foreign_key", f"{table.name}.{col} -> {ref_t}.{ref_c} is not declared in the database")
    for col, ref_t, ref_c in sorted(found - expected):
        report.issue("unknown_foreign_key", f"{table.name}.{col} -> {ref_t}.{ref_c} is not in the models")


def _find_orphans(conn, report, metadata, present):
    """Rows whose foreign-key value points at nothing. Checked from the MODELS'
    foreign keys, so it works even when the database lacks the FK declaration,
    and even though SQLite enforcement is off."""
    for table in metadata.sorted_tables:
        if table.name not in present:
            continue
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({quote_ident(table.name)})")]
        for col, ref_t, ref_c in sorted(_expected_fks(table)):
            if ref_t not in present or col not in cols:
                continue
            sql = (f"SELECT c.rowid, c.* FROM {quote_ident(table.name)} c "
                   f"LEFT JOIN {quote_ident(ref_t)} p ON p.{quote_ident(ref_c)} = c.{quote_ident(col)} "
                   f"WHERE c.{quote_ident(col)} IS NOT NULL AND p.{quote_ident(ref_c)} IS NULL ORDER BY c.rowid")
            for row in conn.execute(sql):
                report.orphans.append({
                    "table": table.name, "rowid": row[0], "column": col,
                    "missing": f"{ref_t}.{ref_c} = {row[1 + cols.index(col)]}",
                    "row": dict(zip(cols, row[1:])),
                })
    # SQLite's own check, for FKs declared in the database (catches anything the models don't know about).
    for t, rowid, parent, _fkid in conn.execute("PRAGMA foreign_key_check"):
        if not any(o["table"] == t and o["rowid"] == rowid for o in report.orphans):
            report.orphans.append({"table": t, "rowid": rowid, "column": "?", "missing": f"row in {parent}", "row": {}})
    if report.orphans:
        report.issue("orphaned_rows", f"{len(report.orphans)} row(s) point at rows that don't exist (listed under orphans)")


def _summary(conn, present):
    """Facts that help tell which database is the real, current one."""
    s = {}
    if "orders" in present:
        s["orders_first_date"], s["orders_last_date"] = conn.execute("SELECT MIN(order_date), MAX(order_date) FROM orders").fetchone()
    if "products" in present:
        s["batches_last_added"] = conn.execute("SELECT MAX(date_added) FROM products").fetchone()[0]
        s["product_names"] = [r[0] for r in conn.execute("SELECT DISTINCT product_name FROM products ORDER BY 1")]
    if "users" in present:
        s["usernames"] = [r[0] for r in conn.execute("SELECT username FROM users ORDER BY id")]
    return s


def preflight(path: Path) -> Report:
    path = Path(path)
    stat = path.stat() if path.exists() else None
    conn = connect_readonly(path)  # raises DatabaseToolError if missing / not SQLite / journal present
    try:
        report = Report(
            database=str(path.resolve()),
            checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            file_size=stat.st_size,
            file_sha256=file_sha256(path),
        )
        report.integrity = integrity(conn)
        if report.integrity != "ok":
            report.issue("integrity", f"PRAGMA integrity_check: {report.integrity}")
        metadata = _expected()
        present = set(user_tables(conn))
        report.row_counts = row_counts(conn)
        if "alembic_version" in present:
            rev = conn.execute("SELECT version_num FROM alembic_version").fetchall()
            report.revision = ",".join(r[0] for r in rev) or None

        for name in sorted(set(metadata.tables) - present):
            report.issue("missing_table", f"table {name} is missing")
        for name in sorted(present - set(metadata.tables) - ALLOWED_EXTRA_TABLES):
            hint = f" ({HINTS[name]})" if name in HINTS else ""
            report.issue("unknown_table", f"table {name} is not in the models{hint}")
        for table in metadata.sorted_tables:
            if table.name in present:
                _check_columns(conn, report, table)
                _check_indexes(conn, report, table)
                _check_foreign_keys(conn, report, table)
        _find_orphans(conn, report, metadata, present)
        report.summary = _summary(conn, present)
    finally:
        conn.close()
    return report


def format_report(r: Report) -> str:
    lines = [
        f"Database:   {r.database}",
        f"Size:       {r.file_size:,} bytes   sha256 {r.file_sha256[:16]}…",
        f"Integrity:  {r.integrity}",
        f"Revision:   {r.revision or 'not under migration control'}",
        f"Rows:       {r.row_counts}",
    ]
    for k, v in r.summary.items():
        lines.append(f"  {k}: {v}")
    lines.append("")
    if r.known_legacy:
        lines.append("Known legacy differences (expected in older databases; planned migration fixes them):")
        lines += [f"  - {d['table']}.{d['column']} {d['difference']}: {d['found']} -> {d['expected']} ({d['reason']})"
                  for d in r.known_legacy]
    if r.issues:
        lines.append(f"ISSUES ({len(r.issues)}):")
        lines += [f"  - [{i['kind']}] {i['message']}" for i in r.issues]
    if r.orphans:
        lines.append("Orphaned rows (investigate each one; nothing has been changed):")
        lines += [f"  - {o['table']} rowid {o['rowid']}: {o['column']} -> missing {o['missing']}  {o['row']}" for o in r.orphans]
    lines.append("")
    lines.append("RESULT: no issues" if r.ok else "RESULT: issues found, resolve before migrating")
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Read-only health check of a FoodHeroes SQLite database.")
    p.add_argument("database", nargs="?", type=Path, default=None, help="default: from DATABASE_URL")
    p.add_argument("--json", action="store_true", help="print the report as JSON")
    args = p.parse_args(argv)
    try:
        report = preflight(args.database or default_db_path())
    except DatabaseToolError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({**asdict(report), "ok": report.ok}, indent=2, default=str))
    else:
        print(format_report(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
