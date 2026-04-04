# File-by-File Walkthrough

This walkthrough helps a new engineer locate the right code quickly.

## Entry Points

### `main.py` (repo root)

CLI wrapper for running uvicorn. Supports:

- `--host`
- `--port`
- `--reload`
- `--data-dir` (sets `KLIN_APP_DATA_DIR`)

### `app/main.py`

Application assembly:

1. Creates singleton service instances (`RagService`, `TextCache`, parser, ingest worker).
2. Defines lifespan startup/shutdown.
3. Mounts routers.
4. Exposes `/health`.

When debugging startup failures, start here first.

## Core Configuration

### `app/core/config.py`

Single settings source via `pydantic-settings` and `KLIN_` prefix.

Most important fields for local development:

- `database_path`
- `rag_working_dir`
- `llama_server_url`
- `embedding_dim_size`
- `summary_max_tokens`
- `rename_max_tokens`
- `classification_top_k`

Storage path behavior differs between source run and frozen bundle. Read this file before changing environment assumptions.

## Data Layer

### `app/db/models.py`

Defines current tables:

1. `app_settings`
2. `watched_folders`
3. `categories`
4. `files`
5. `file_analysis`
6. `category_scores`
7. `history_logs`
8. `system_logs`

### `app/db/session.py`

- async engine
- `get_db` dependency with commit/rollback behavior

### `app/db/migrations.py` and `alembic/versions/*`

Schema migration entrypoint and versions.

## API Layer

### `app/api/organize.py`

Primary business workflow.

- `POST /api/organize`
- `POST /api/organize/apply`

Includes cache branches, AI checks, ingest enqueue, analysis persistence, classification, and history writes.

### `app/api/summary.py`

Summary endpoints:

- `POST /api/summary`
- `POST /api/summary/stream`

### `app/api/settings/*`

Settings domain:

- categories CRUD + batch
- default base path + onboarding status
- auto organize settings + watched folders

### `app/api/history.py`

History list/read and note history creation.

### `app/api/search.py`

Current mock search endpoint used by UI integration.

## Service Layer

### AI services

- `app/services/ai/llm_client.py`
- `app/services/ai/rag_service.py`
- `app/services/ai/summary_service.py`
- `app/services/ai/rename_service.py`

### File and ingest services

- `app/services/files/scanner_service.py`
- `app/services/files/docling_parser.py`
- `app/services/files/text_cache.py`
- `app/services/background_ingest.py`

### Category services

- `app/services/categories/classification_service.py`
- `app/services/categories/seed_service.py`
- `app/services/categories/category_embedding_text.py`

### Workflow and utility services

- `app/services/summary_workflow_service.py`
- `app/services/history_service.py`
- `app/services/system_log_service.py`
- `app/services/startup_checks.py`

## Typical Debug Paths

1. Organize response wrong or empty
   - `app/api/organize.py`
   - `app/services/files/scanner_service.py`
   - `app/services/categories/classification_service.py`

2. Summary quality issue
   - `app/services/summary_workflow_service.py`
   - `app/services/ai/summary_service.py`
   - `app/services/files/docling_parser.py`

3. Startup degraded health
   - `app/main.py`
   - `app/services/startup_checks.py`
   - `app/services/ai/llm_client.py`

4. Category behavior issue
   - `app/api/settings/categories.py`
   - `app/services/categories/seed_service.py`

Continue with [04-services.md](./04-services.md).
