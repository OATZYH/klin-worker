# Alembic Migration Guide

> Database schema migration workflow for **klin-worker** using [Alembic](https://alembic.sqlalchemy.org/) + [SQLModel](https://sqlmodel.tiangolo.com/).

---

## Overview

Alembic manages incremental, reversible database schema changes through versioned migration scripts. Every schema change goes through this flow:

```
Edit SQLModel models → Generate migration → Review → Apply (upgrade)
```

---

## Project Structure

```
klin-worker/
├── alembic.ini                     # Alembic configuration
├── alembic/
│   ├── env.py                      # Runtime environment (DB connection, metadata)
│   ├── script.py.mako              # Template for generated migration files
│   ├── README                      # Alembic readme
│   └── versions/                   # Migration scripts (auto-generated)
│       └── 6e4d8a525088_initial_schema.py
└── app/
    └── db/
        ├── models.py               # SQLModel table definitions (source of truth)
        ├── session.py              # Async engine + session factory
        └── migrations.py           # Startup hook (runs `alembic upgrade head`)
```

---

## Common Commands

All commands are run from the project root with `uv run`.

### Check Current Revision

```bash
uv run alembic current
```

Shows which migration the database is currently at.

### Create a New Migration (autogenerate)

After editing models in `app/db/models.py`:

```bash
uv run alembic revision --autogenerate -m "describe_the_change"
```

**Examples:**

```bash
uv run alembic revision --autogenerate -m "add_tags_to_files"
uv run alembic revision --autogenerate -m "add_user_table"
uv run alembic revision --autogenerate -m "rename_hash_to_checksum"
```

> ⚠️ **Always review** the generated file in `alembic/versions/` before applying. Autogenerate detects most changes but may miss renames or data migrations.

### Apply Migrations (upgrade)

```bash
# Apply all pending migrations
uv run alembic upgrade head

# Apply one revision forward
uv run alembic upgrade +1
```

### Revert Migrations (downgrade)

```bash
# Revert the last migration
uv run alembic downgrade -1

# Revert to a specific revision
uv run alembic downgrade <revision_id>

# Revert all migrations (back to empty DB)
uv run alembic downgrade base
```

### View Migration History

```bash
uv run alembic history --verbose
```

### Show Pending Migrations

```bash
# Show SQL that would be generated (without executing)
uv run alembic upgrade head --sql
```

---

## Step-by-Step: Adding a New Table

### 1. Define the model

Edit `app/db/models.py`:

```python
class Tag(SQLModel, table=True):
    __tablename__ = "tags"

    id: str = Field(default_factory=_new_uuid, primary_key=True)
    name: str = Field(nullable=False, unique=True)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
```

### 2. Generate the migration

```bash
uv run alembic revision --autogenerate -m "add_tags_table"
```

### 3. Review the generated file

Open `alembic/versions/<hash>_add_tags_table.py` and verify:
- `upgrade()` contains the correct `CREATE TABLE`
- `downgrade()` contains `DROP TABLE`

### 4. Apply the migration

```bash
uv run alembic upgrade head
```

---

## Step-by-Step: Adding a Column

### 1. Add the field to the model

```python
class File(SQLModel, table=True):
    # ... existing fields ...
    tags: Optional[str] = Field(default=None)  # new column
```

### 2. Generate + apply

```bash
uv run alembic revision --autogenerate -m "add_tags_column_to_files"
uv run alembic upgrade head
```

---

## Step-by-Step: Removing a Column

### 1. Remove the field from the model

Delete the field from `app/db/models.py`.

### 2. Generate + apply

```bash
uv run alembic revision --autogenerate -m "remove_tags_from_files"
uv run alembic upgrade head
```

---

## SQLite Limitations

This project uses SQLite with **batch mode** (`render_as_batch=True` in `env.py`). This is required because SQLite does not natively support:

- `ALTER TABLE ... DROP COLUMN` (before SQLite 3.35)
- `ALTER TABLE ... RENAME COLUMN` (before SQLite 3.25)
- Adding/removing foreign key constraints

Alembic's batch mode works around this by recreating the table with the new schema and migrating data automatically.

---

## Automatic Migrations at Startup

The application runs migrations automatically at startup via `app/db/migrations.py`:

```python
async def run_migrations() -> None:
    command.upgrade(cfg, "head")
```

This is called from the FastAPI lifespan hook in `app/main.py`. You do **not** need to run `alembic upgrade head` manually during development if the server is restarted.

---

## Configuration

### `alembic.ini`

| Setting | Value | Notes |
|---|---|---|
| `script_location` | `%(here)s/alembic` | Migration scripts directory |
| `sqlalchemy.url` | `sqlite:///...` | Overridden at runtime by `env.py` |
| `prepend_sys_path` | `.` | Ensures app modules are importable |

### `alembic/env.py`

Key configuration:

- **`target_metadata`** — Set to `SQLModel.metadata` so autogenerate can compare models to the DB.
- **`render_as_batch=True`** — Enables SQLite batch mode for all migrations.
- **DB URL** — Dynamically read from `app.core.config.settings` (strips `+aiosqlite` for sync Alembic operations).

---

## Troubleshooting

### "Target database is not up to date"

```bash
uv run alembic stamp head    # Mark DB as current without running migrations
```

### Empty migration generated (no changes detected)

- Make sure `app.db.models` is imported in `env.py`
- Ensure you saved `models.py` before running autogenerate
- Check that the model class has `table=True`

### "Can't drop column in SQLite"

Ensure `render_as_batch=True` is set in both `run_migrations_offline()` and `run_migrations_online()` in `env.py`. This is already configured.

### Reset the database completely

```bash
rm .storage/klin.db
uv run alembic upgrade head
```
