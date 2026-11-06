"""
Back up, verify and restore the FoodHeroes SQLite database.

Run from backend/ with the app STOPPED:

    python -m scripts.backup_db backup                       # uses DATABASE_URL (required; no default)
    python -m scripts.backup_db backup --source path/to/foodheroes.db --dest-dir backups
    python -m scripts.backup_db verify backups/foodheroes-20261105-174500.db
    python -m scripts.backup_db restore backups/foodheroes-20261105-174500.db --target restored.db

Why this matters: a verified backup is the PRIMARY way to recover from a bad
migration or a damaged database. Alembic downgrades and SQLite transactions
are not a guaranteed undo (see docs/DATABASE_RECOVERY.md).

What a backup is:
  - a consistent copy made with SQLite's backup API (safe even while the file
    is open, unlike a plain file copy), from a READ-ONLY connection
  - checked with PRAGMA integrity_check
  - row counts compared table by table with the source
  - a manifest (<backup>.json) with the file's SHA-256, a digest of every
    row, and the row counts, so the backup can be re-verified at any time

The source database is never written to. Existing files are never overwritten.
"""
import argparse
import json
import os
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from scripts.db_common import (
    DatabaseToolError,
    connect_readonly,
    content_digest,
    default_db_path,
    file_sha256,
    integrity,
    row_counts,
)

MANIFEST_VERSION = 1


@dataclass
class BackupResult:
    source: str
    backup: str
    manifest: str
    created_at: str
    sha256: str
    content_digest: str
    row_counts: dict
    integrity: str


def manifest_path(backup: Path) -> Path:
    return backup.with_name(backup.name + ".json")


def _new_backup_path(source: Path, dest_dir: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    candidate = dest_dir / f"{source.stem}-{stamp}.db"
    n = 1
    while candidate.exists() or manifest_path(candidate).exists() or Path(str(candidate) + ".partial").exists():
        candidate = dest_dir / f"{source.stem}-{stamp}-{n}.db"
        n += 1
    return candidate


def _copy(src_conn: sqlite3.Connection, dest: Path) -> None:
    """SQLite online backup into a brand-new file."""
    if dest.exists():
        raise DatabaseToolError(f"Refusing to overwrite {dest}")
    out = sqlite3.connect(dest)
    try:
        src_conn.backup(out)
    finally:
        out.close()


def backup(source: Path, dest_dir: Path) -> BackupResult:
    source, dest_dir = Path(source).resolve(), Path(dest_dir).resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)
    final = _new_backup_path(source, dest_dir)
    if final.resolve() == source:
        raise DatabaseToolError("The backup can't be the source file itself")
    partial = Path(str(final) + ".partial")

    src = connect_readonly(source)
    try:
        source_counts = row_counts(src)
        source_digest = content_digest(src)
        _copy(src, partial)
    finally:
        src.close()

    # Verify the copy before giving it its real name. A failed copy stays as
    # .partial so it can be inspected; it's never mistaken for a good backup.
    copy = connect_readonly(partial)
    try:
        check = integrity(copy)
        copy_counts = row_counts(copy)
        copy_digest = content_digest(copy)
    finally:
        copy.close()
    if check != "ok":
        raise DatabaseToolError(f"Backup failed integrity check: {check} (left at {partial})")
    if copy_counts != source_counts or copy_digest != source_digest:
        raise DatabaseToolError(
            "Backup doesn't match the source. The database probably changed during the "
            f"backup: stop the app and try again. (left at {partial})"
        )

    os.replace(partial, final)
    result = BackupResult(
        source=str(source),
        backup=str(final),
        manifest=str(manifest_path(final)),
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        sha256=file_sha256(final),
        content_digest=copy_digest,
        row_counts=copy_counts,
        integrity=check,
    )
    with open(result.manifest, "x", encoding="utf-8") as f:
        json.dump({"manifest_version": MANIFEST_VERSION, **asdict(result)}, f, indent=2)
    return result


def verify_backup(backup_file: Path) -> dict:
    """Re-check a backup against its manifest. Raises DatabaseToolError on any mismatch."""
    backup_file = Path(backup_file)
    mpath = manifest_path(backup_file)
    if not mpath.is_file():
        raise DatabaseToolError(f"No manifest next to {backup_file} ({mpath.name})")
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    problems = []
    if file_sha256(backup_file) != manifest["sha256"]:
        problems.append("file checksum differs from the manifest (the backup was changed)")
    conn = connect_readonly(backup_file)
    try:
        check = integrity(conn)
        counts = row_counts(conn)
        digest = content_digest(conn)
    finally:
        conn.close()
    if check != "ok":
        problems.append(f"integrity check: {check}")
    if counts != manifest["row_counts"]:
        problems.append(f"row counts {counts} differ from manifest {manifest['row_counts']}")
    if digest != manifest["content_digest"]:
        problems.append("row contents differ from the manifest")
    if problems:
        raise DatabaseToolError(f"Backup {backup_file.name} failed verification: " + "; ".join(problems))
    return manifest


def restore(backup_file: Path, target: Path, replace: bool = False) -> dict:
    """Restore a verified backup to `target`.

    By default only restores to a path that doesn't exist yet (e.g. a scratch
    file for a restore drill). With replace=True the existing target is first
    renamed to <target>.before-restore-<timestamp>, never deleted.
    The app must be stopped before replacing the live database.
    """
    backup_file, target = Path(backup_file), Path(target)
    manifest = verify_backup(backup_file)
    moved_aside = None
    if target.exists():
        if not replace:
            raise DatabaseToolError(f"{target} already exists. Use --replace to restore over it.")
        for suffix in ("-journal", "-wal"):
            if Path(str(target) + suffix).exists():
                raise DatabaseToolError(f"{target.name}{suffix} exists: stop the app before restoring.")
    staging = Path(str(target) + ".restoring")
    if staging.exists():
        raise DatabaseToolError(f"{staging} exists from an earlier attempt; inspect it, then remove it")

    src = connect_readonly(backup_file)
    try:
        _copy(src, staging)
    finally:
        src.close()
    check_conn = connect_readonly(staging)
    try:
        check, digest = integrity(check_conn), content_digest(check_conn)
    finally:
        check_conn.close()
    if check != "ok" or digest != manifest["content_digest"]:
        raise DatabaseToolError(f"Restored copy failed verification; nothing replaced (left at {staging})")

    if target.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        moved_aside = Path(f"{target}.before-restore-{stamp}")
        os.replace(target, moved_aside)
    os.replace(staging, target)
    return {"restored": str(target), "from": str(backup_file),
            "moved_aside": str(moved_aside) if moved_aside else None,
            "row_counts": manifest["row_counts"]}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Back up, verify and restore the FoodHeroes SQLite database.")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backup", help="make a verified backup")
    b.add_argument("--source", type=Path, default=None, help="database file (default: from DATABASE_URL)")
    b.add_argument("--dest-dir", type=Path, default=Path("backups"))
    v = sub.add_parser("verify", help="re-check a backup against its manifest")
    v.add_argument("backup", type=Path)
    r = sub.add_parser("restore", help="restore a verified backup")
    r.add_argument("backup", type=Path)
    r.add_argument("--target", type=Path, required=True)
    r.add_argument("--replace", action="store_true", help="restore over an existing file (it is moved aside, not deleted)")
    args = p.parse_args(argv)
    try:
        if args.cmd == "backup":
            res = backup(args.source or default_db_path(), args.dest_dir)
            print(f"Backup OK: {res.backup}")
            print(f"  integrity: {res.integrity}   rows: {res.row_counts}")
            print(f"  sha256: {res.sha256}")
            print(f"  manifest: {res.manifest}")
        elif args.cmd == "verify":
            m = verify_backup(args.backup)
            print(f"Backup verified: {args.backup}   rows: {m['row_counts']}")
        else:
            res = restore(args.backup, args.target, args.replace)
            print(f"Restored {res['from']} -> {res['restored']}   rows: {res['row_counts']}")
            if res["moved_aside"]:
                print(f"  previous file kept at {res['moved_aside']}")
    except DatabaseToolError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
