# Configuring the Backend

The backend reads its settings from environment variables. It checks every one when it starts and **refuses to start** if anything is missing or unsafe. Nothing falls back to a built-in secret or a default database.

## The settings

| Setting | Required? | What it is | Rules |
|---|---|---|---|
| `DATABASE_URL` | **Yes** | Which database to use | SQLite: the **full** path, with four slashes: `sqlite:////Users/you/FoodHeroes-data/foodheroes.db`. A relative path (`sqlite:///./foodheroes.db`) is refused, because it would mean a different file depending on which folder you start from. PostgreSQL later: `postgresql+psycopg://user:password@host:5432/foodheroes`. No default. |
| `SECRET_KEY_FILE` | **One of these two** | Full path of a private file holding the signing key, on one line | The file must be readable only by you (`chmod 600`). Recommended: the key stays in one file and never appears in a settings file, a terminal or a screenshot. |
| `SECRET_KEY` | | The signing key itself | At least 32 random characters. Not the old published default or a placeholder. If both are set, the backend refuses: it won't guess which one you meant. |
| `TOKEN_MINUTES` | No | How long a login lasts | 5 to 720 minutes. Default 480 (8 hours). |
| `FRONTEND_ORIGINS` | No | The website address(es) allowed to call the API from a browser | Exact origins, comma separated: `https://app.example.com,http://localhost:5173`. No `*`, no path or trailing slash. Default `http://localhost:5173`. |
| `LOW_STOCK_THRESHOLD` | No | Cartons at or below which a product shows a low-stock alert | A whole number. Default 10. Not a security setting. |

The signing key decides who is logged in: anyone who has it can create a valid login as any user, including an admin. Treat it like the master password to the business data.

A few tools work without these settings, because they never sign logins and are told the database explicitly: `create_admin` and `reset_password` (both take `--database`). Everything else (the backend, `alembic`, `import_ledger`, and the backup and preflight tools when you don't give them a path) needs `DATABASE_URL`.

## Running it on your computer

All the real settings live in one file **outside the repository**, next to the database: `~/FoodHeroes-data/foodheroes.env`. Every command runs through `scripts/with_env.py`, which loads that file and checks it before running anything.

### One-time setup

```bash
cd ~/Documents/food-inventory-tracker/backend
source .venv/bin/activate

# 1. A signing key in its own private file (skip if you already have one).
#    Creating it doesn't show it, and you never need to look at it.
ls ~/FoodHeroes-data/secret_key 2>/dev/null || \
  ( umask 077; python3 -c "import secrets; print(secrets.token_urlsafe(48))" > ~/FoodHeroes-data/secret_key )
chmod 600 ~/FoodHeroes-data/secret_key

# 2. The settings file, from the template.
cp .env.example ~/FoodHeroes-data/foodheroes.env
chmod 600 ~/FoodHeroes-data/foodheroes.env
echo "$HOME"        # your full home folder, e.g. /Users/taiye: you need it in the next step
```

3. Open `~/FoodHeroes-data/foodheroes.env` in a text editor. Replace each `/REPLACE/WITH/FULL/PATH/TO` with your home folder, for example:

   ```
   DATABASE_URL=sqlite:////Users/taiye/FoodHeroes-data/foodheroes.db
   SECRET_KEY_FILE=/Users/taiye/FoodHeroes-data/secret_key
   TOKEN_MINUTES=480
   FRONTEND_ORIGINS=http://localhost:5173
   ```

   Write paths out in full. `~` and `$HOME` are **not** expanded in this file, and are refused rather than guessed.

4. Check it. This only validates; it starts nothing and changes nothing:

   ```bash
   python -m scripts.with_env ~/FoodHeroes-data/foodheroes.env -- python -c "print('settings OK')"
   ```

   It prints the settings it loaded. The database URL's password (if any) is shown as `***`, and the key is never shown.

### Every day

From `backend/`, with the virtual environment active:

```bash
python -m scripts.with_env ~/FoodHeroes-data/foodheroes.env -- python -m uvicorn app.main:app
```

The same prefix works for every other command, so they all use the same database:

```bash
E=~/FoodHeroes-data/foodheroes.env
python -m scripts.with_env $E -- python -m scripts.db_preflight
python -m scripts.with_env $E -- python -m scripts.backup_db backup --dest-dir ~/FoodHeroes-backups
python -m scripts.with_env $E -- alembic upgrade head          # only after a backup: docs/DATABASE_RECOVERY.md
```

To save typing, you can add a line like this to `~/.zshrc`:

```bash
alias fh='python -m scripts.with_env ~/FoodHeroes-data/foodheroes.env --'
# then:  fh python -m uvicorn app.main:app
```

### What `with_env` refuses, and why

| It says | Why | Fix |
|---|---|---|
| "is inside the repository" | A settings file in the repo is one `git add .` away from being published | Keep it in `~/FoodHeroes-data/` |
| "can be read by other users" | Other accounts on the computer could read the key or paths | `chmod 600` the file |
| "unknown setting" | Usually a typo (`SECERT_KEY`), which would otherwise leave the real setting missing | Fix the name |
| "already set in this shell to a different value" | An old `export DATABASE_URL=…` would silently win over the file and open a different database | `unset` it, or open a new terminal |
| "looks like it uses a shell variable or ~" | The file is read as plain text, never run as shell code | Write the full path |
| "isn't ready to use" plus the reason | The same check the backend does at startup | Follow the message |

Plain `source`-ing an env file, or `uvicorn --env-file`, skips all of these. In particular they both let a value already exported in your shell win over the file without a word.

### Rotating the signing key

Replacing the key logs everyone out at once, so do it after a token or the key may have been seen by someone else:

```bash
( umask 077; python3 -c "import secrets; print(secrets.token_urlsafe(48))" > ~/FoodHeroes-data/secret_key )
```

Then restart the backend. (With `SECRET_KEY_FILE` the new key is used from the next request even without a restart, but restarting makes it obvious.) Everyone logs in again.

## Running it on a host (production)

The pilot runs locally. When it moves to a host, the same settings come from the host instead of a file:

- **Set them in the host's own secrets or environment settings.** Examples: Render or Railway "environment variables", Fly.io `fly secrets set`, a cloud secret manager (AWS Secrets Manager, Google Secret Manager, Azure Key Vault), or Docker/Kubernetes secrets. Don't use `with_env` or an env file on a server: the platform supplies the variables directly, and the backend's startup checks still apply.
- **Prefer a mounted secret file for the key.** Docker and Kubernetes put secrets in files such as `/run/secrets/foodheroes_key`. Set `SECRET_KEY_FILE=/run/secrets/foodheroes_key`, and the key never appears in the container's environment, `docker inspect` output or crash reports.
- **One key per environment.** The pilot computer, staging and production each get their own key, generated there. Never copy the local key to a server, or a server key to a laptop.
- **The database password belongs in the secret store too.** It's part of `DATABASE_URL` for PostgreSQL. The backend shows it as `***` in every message it writes, but the host's dashboard and logs are outside the backend's control: use the platform's "secret" type, not a plain variable, where it offers one.
- **Never in the image or the repository.** Don't `COPY` an env file into a Docker image, and don't put values in `docker-compose.yml`, CI configuration or commit messages. In CI, use the CI's masked secrets.
- **HTTPS only.** `FRONTEND_ORIGINS` should list only the production site's `https://` address.
- **Who can see them.** Limit dashboard access to the people who deploy, and rotate the key and database password when someone with access leaves.

## What the backend never writes out

- **Error messages** from configuration checks describe the problem, never the value: never the key, the key file's contents, or a database password.
- **Validation errors (HTTP 422)** don't repeat submitted values, so they never include a password (`app/errors.py`).
- **Database errors** show `[SQL parameters hidden]` instead of the values being written (`hide_parameters`).
- **Startup output** (`with_env`, `alembic`) shows the database URL with its password as `***`.
- **Server access logs** (uvicorn) record the method and path of each request, not bodies or headers. Never put a secret in a URL.

## Checking for exposure

Run this from the repository root before pushing. Both commands print only file **names**, never contents:

```bash
git status --ignored --short | grep -E '\.env|secret|\.key|\.pem'   # real settings must show as ignored (!!) or not at all
git log --all --name-only --format= | grep -E '(^|/)(\.env[^.]*|.*\.env|secret_key)$' | sort -u   # must print nothing
```

If a real key or settings file was ever committed, rotate the key straight away. Removing the file in a new commit doesn't remove it from history.
