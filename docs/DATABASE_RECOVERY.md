# Database Backup and Recovery

This covers the SQLite database used in development and the pilot. PostgreSQL gets its own procedure (`pg_dump` plus the host's point-in-time recovery) when it's deployed.

## The rule

**A verified backup is the primary recovery mechanism.** Take one before every migration, every bulk import, and any manual change to the data.

Do not rely on rolling back:

- **SQLite migration rollback is not guaranteed.** By default, Python's `sqlite3` driver commits schema changes (`CREATE`, `ALTER`, `DROP`) immediately, and Alembic treats SQLite as non-transactional, so a migration that fails partway would leave half its changes behind. `migrations/env.py` prevents that by running each migration inside an explicit `BEGIN ... COMMIT`, with foreign keys switched off while it runs and checked before it commits (`tests/test_migrations.py` proves a failed migration leaves the database unchanged). That covers a migration that *fails*. It can't help when a migration *succeeds* and the result is wrong: then there is nothing to roll back to.
- **Alembic `downgrade` is not a recovery tool.** It reverses the *schema*. It can't bring back rows a migration deleted, or values it converted, and a downgrade nobody has run before is untested.
- **Rebuilding a table can quietly break foreign keys.** On modern SQLite, renaming a table rewrites the foreign keys in *other* tables to follow the new name. A rename-copy-drop rebuild can leave a foreign key pointing at a table that no longer exists. This is easy to miss, and the preflight check is there to catch it.
- **Hosting is not a backup.** A cloud disk protects against hardware failure, not against the app or a person writing wrong data.

## Tools

Run from `backend/` with the virtual environment active. **Stop the app first.** The tools refuse to run if they find a `-journal` or `-wal` file next to the database, because that means the app is running or a write was interrupted.

| Command | What it does | Writes to |
|---|---|---|
| `python -m scripts.db_preflight [DB]` | Read-only health check: integrity, row counts, schema compared with the models, indexes, foreign keys, orphaned rows | Nothing |
| `python -m scripts.backup_db backup [--source DB] [--dest-dir backups]` | Consistent copy using SQLite's backup API, integrity-checked, row counts and row contents compared with the source, plus a manifest (`<backup>.json`) | A **new** file in `backups/` |
| `python -m scripts.backup_db verify BACKUP` | Re-checks a backup against its manifest: file checksum, integrity, row counts, row contents | Nothing |
| `python -m scripts.create_admin --database DB --username NAME --fullname "FULL NAME" --email EMAIL` | Creates the first administrator (asks for the password twice, hidden). Adds exactly one account and changes nothing else. Refuses if an administrator already exists, and refuses backups and databases that aren't at the latest migration | The live database's `users` table (one new row) |
| `python -m scripts.reset_password --database DB --username NAME` | Sets a new password for an existing user (asks twice, hidden). Changes only that user's password hash. Refuses backups and databases that aren't at the latest migration | The live database's `users` table |
| `python -m scripts.backup_db restore BACKUP --target PATH` | Restores a verified backup to a path that doesn't exist yet (a restore drill) | `PATH` |
| `... restore BACKUP --target foodheroes.db --replace` | Restores over the live database. The current file is **renamed** to `foodheroes.db.before-restore-<time>`, never deleted | The database file |

`DB` defaults to the file in `DATABASE_URL` (normally `./foodheroes.db`). Backups, manifests and restore leftovers are git-ignored. **Never commit them: they contain real business data.**

All tools open the source database read-only (SQLite `mode=ro` plus `PRAGMA query_only`). No tool overwrites or deletes an existing file.

## Setting up a database

The app never creates or changes tables. At startup it checks the database and refuses to serve requests unless it's at the latest migration. The error tells you which case you're in:

| Startup says | Meaning | What to do |
|---|---|---|
| "No database at …" / "has no FoodHeroes tables" | New installation | [Fresh installation](#fresh-installation) |
| "isn't under migration control yet" | A database created before migrations existed | [Bringing an existing database under migrations](#bringing-an-existing-database-under-migrations) |
| "is at migration X; this code needs Y" | The code is newer than the database | [Upgrading after pulling new code](#upgrading-after-pulling-new-code) |
| "at migration X, which this code doesn't know about" | The database was migrated by newer code | Update the code. Don't change the database |
| "has no administrator account yet" | Migrated, but nobody can log in to manage it | [Creating the first administrator](#creating-the-first-administrator) |

Migrations are never run automatically. `alembic` refuses to run unless `DATABASE_URL` is set, so it can't touch a database by accident. Always give the **full path** (`sqlite:////Users/you/…/foodheroes.db`, or `sqlite:///$PWD/foodheroes.db` from the folder it's in): a relative path depends on which folder you run the command from.

### Fresh installation

```bash
cd backend && source venv/bin/activate
export DATABASE_URL=sqlite:///$PWD/foodheroes.db
alembic upgrade head
python -m scripts.db_preflight          # expect: Revision 0002 (or later), RESULT: no issues
python -m scripts.create_admin --database "$PWD/foodheroes.db" \
    --username owner --fullname "Business Owner" --email owner@example.com
uvicorn app.main:app --reload
```

### Creating the first administrator

The app never creates an account by itself and has no default password. It refuses to start until the database has at least one administrator, and tells you to run:

```bash
python -m scripts.create_admin --database /full/path/foodheroes.db \
    --username owner --fullname "Business Owner" --email owner@example.com
```

1. Stop the app. The database must already be at the latest migration (`alembic upgrade head`).
2. Run the command above with your own details. All four options are required; there is no default database.
3. Type the password twice. Nothing appears as you type, and it's never shown, logged or saved anywhere except as a bcrypt hash. It must follow the [password rules](#password-rules). Store it in a password manager.
4. Start the app and log in with the new account.

What it changes: one new row in `users`, with role Admin. It never changes, replaces or deletes an existing account and touches no other table.

It refuses, and changes nothing, for: a database that already has an administrator (use [Resetting a password](#resetting-a-password) to get back into an existing account), a username that already exists, an invalid username, name or email, a path that doesn't exist (it's never created), a file that isn't an SQLite database, a backup, a database that isn't at the latest migration, and a database with a journal file beside it (the app may be running). If another administrator appears while it's waiting for the password, it undoes its own change.

### Bringing an existing database under migrations

For a database the app created before migrations existed, once only. **Never run `alembic upgrade head` on it first**: the baseline migration would refuse anyway, because the tables already exist.

1. Stop the app.
2. `python -m scripts.db_preflight` must report `RESULT: no issues`. "Known legacy differences" are fine: migration 0002 fixes them. Anything listed under ISSUES must be investigated first.
3. `python -m scripts.backup_db backup`, then copy the backup and its `.json` manifest off this computer.
4. `alembic stamp 0001`. This only records that the tables match the baseline. No table is changed.
5. `alembic upgrade head`. Migration 0002 rebuilds only tables with old column types; whole numbers like `20` become `20.0`.
6. `python -m scripts.db_preflight` must report the latest revision, no legacy differences, no issues, and the same row counts as step 2.
7. Start the app.

If anything looks wrong at step 5 or 6, stop and restore the backup from step 3 (see [Recovering](#recovering-from-a-bad-migration-or-damaged-database)).

### Upgrading after pulling new code

1. Stop the app.
2. `python -m scripts.backup_db backup`
3. `alembic upgrade head`
4. `python -m scripts.db_preflight` must report the latest revision and no issues.
5. Start the app.

## Before a migration

1. Stop the app.
2. `python -m scripts.db_preflight` and read the result.
   - `no issues` (possibly with "known legacy differences") means you can continue.
   - `issues found` means stop. Investigate each one. Orphaned rows are listed with their full contents, and none are fixed automatically.
3. `python -m scripts.backup_db backup` and note the backup file name.
4. Copy the backup and its `.json` manifest somewhere off this computer (a USB drive or cloud storage).
5. Run the migration.
6. `python -m scripts.db_preflight` again. Row counts should match the backup's, unless the migration was meant to change them.

## Restore drill (do this before trusting a backup)

```bash
python -m scripts.backup_db verify backups/foodheroes-<time>.db
python -m scripts.backup_db restore backups/foodheroes-<time>.db --target /tmp/restore-drill.db
python -m scripts.db_preflight /tmp/restore-drill.db
```

The restored copy must pass the preflight check, and its row counts must match the manifest. Delete the drill copy afterwards.

## Password rules

One set of rules, in `app/passwords.py`, applies wherever a password is set: `create_admin`, `reset_password`, and adding a user in the app (`POST /users`).

- At least 12 characters.
- At most 72 **bytes**. bcrypt only uses the first 72 bytes, so a longer password is refused rather than silently cut. Plain letters, digits and spaces are 1 byte each; accented letters such as é are 2; other scripts and emoji are 3–4.
- No space (or tab) at the start or end.
- Not the same as the username, ignoring capitals.

Logging in is **not** checked against these rules, so accounts made before them keep working. A login attempt longer than 72 bytes is always wrong (no account can have such a password) and gets the normal "Invalid username or password". It's never cut to 72 bytes, so two passwords that differ only after byte 72 are never treated as the same.

Error messages never contain a password or a password hash: validation errors from the API (HTTP 422) don't repeat the submitted values, and database errors show `[SQL parameters hidden]` instead of the values being written.

## Resetting a password

For a forgotten password. The app has no change-password screen yet.

What changes: only that user's password hash. The account keeps its id, username, role and email. No other table refers to users, so orders, stock and payments can't be affected.

1. Stop the app.
2. Back up the live database:
   `python -m scripts.backup_db backup --source /full/path/foodheroes.db --dest-dir /full/path/backups`
3. `python -m scripts.reset_password --database /full/path/foodheroes.db --username admin`
   - Type the new password twice. Nothing appears as you type, and it's never shown, logged or saved anywhere except as a bcrypt hash.
   - The same rules as everywhere else (see [Password rules](#password-rules)). A few unrelated words is easier to remember than a short complex string. Store it in a password manager, not in a file or note in this repository.
4. `python -m scripts.db_preflight /full/path/foodheroes.db` must still report the latest revision and no issues.
5. Start the app and log in with the new password.

The script refuses (and changes nothing) for: a path that doesn't exist, a backup file, a database that isn't at the latest migration (which includes every pre-migration database and backup), an unknown username, a password that breaks the [password rules](#password-rules) or doesn't match its repeat, and a database with a journal or WAL file beside it (the app may be running).

### Ending existing logins (rotating `SECRET_KEY`)

Changing a password does **not** log out sessions that are already logged in: a login token stays valid until it expires (8 hours, `TOKEN_MINUTES`). If a token may have been seen by someone else (a screenshot, a shared screen), end every session by changing the key that signs tokens:

```bash
( umask 077; python3 -c "import secrets; print(secrets.token_urlsafe(48))" > ~/FoodHeroes-data/secret_key )
export SECRET_KEY="$(cat ~/FoodHeroes-data/secret_key)"
uvicorn app.main:app
```

Every token signed with the old key stops working at once, and everyone logs in again. Start the backend the same way every time. If `SECRET_KEY` isn't set, or is too short, a known default or a placeholder, the backend refuses to start. Keep `secret_key` out of the repository and out of backups you share, and never paste its contents anywhere.

## Recovering from a bad migration or damaged database

1. Stop the app.
2. Don't run anything else against the database. Don't try to "fix it forward" under pressure.
3. `python -m scripts.backup_db verify <latest good backup>`
4. `python -m scripts.backup_db restore <latest good backup> --target foodheroes.db --replace`
5. `python -m scripts.db_preflight` must report no issues, with the expected row counts.
6. Start the app. Keep the `.before-restore-*` file until you understand what went wrong.

Anything recorded **after** that backup is not in it. During the pilot, the notebooks are the record of those transactions; re-enter them from there.
