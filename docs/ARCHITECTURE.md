# Architecture

Technical deep-dive for the current Klin-Worker implementation.

This document describes the architecture that exists in the codebase today, including runtime topology, service boundaries, data model, and end-to-end workflows.

## Table of Contents

- System Overview
- Runtime Topology
- Codebase Structure
- Startup and Shutdown Lifecycle
- Core Workflows
  - Organize Workflow (`POST /api/organize`)
  - Apply Decision Workflow (`POST /api/organize/apply`)
  - Summary Workflow (`POST /api/summary`, `POST /api/summary/stream`)
  - Settings and Onboarding Workflows
  - History Workflow
- Database Schema
- AI and RAG Integration
- Caching and Invalidation Strategy
- Security Model
- Observability and Diagnostics
- Dependency Injection and Service Lifecycles
- API Surface
- Configuration Model
- Current Limits and Planned Extensions

## System Overview

Klin-Worker is a local FastAPI sidecar used by a Tauri desktop application.

Design goals:

1. Privacy-first local processing.
2. No cloud dependency.
3. Read-only analysis during organize flow.
4. Explicit user confirmation before any file move/rename is considered applied.

Current behavior:

- The backend analyzes files from absolute paths.
- The organize endpoint computes summaries, rename suggestions, and category scores.
- The backend does not mutate files on disk in organize.
- User-confirmed actions are recorded via `POST /api/organize/apply` and persisted in DB state/history.

## Runtime Topology

```text
Tauri Frontend (desktop)
        |
        | HTTP localhost
        v
FastAPI (klin-worker)
  - app/main.py lifespan
  - routers under /api
        |
        +--> SQLite (SQLModel + aiosqlite)
        |      - app settings
        |      - categories/files/analysis/scores
        |      - history/system logs
        |
        +--> llama-server (OpenAI-compatible HTTP)
        |      - chat/completions
        |      - embeddings
        |
        +--> RAG-Anything + LightRAG (in-process wrapper)
        |      - semantic storage under rag_working_dir
        |      - embedding function delegates to llama-server
        |
        +--> Background ingest worker
               - inline docling parse
               - async queue for RAG insert
```

Important update vs older architecture docs: LLM is currently out-of-process via `llama-server` HTTP, not in-process `llama-cpp-python` model loading.

## Codebase Structure

High-level modules:

```text
app/
  main.py                     FastAPI app, lifespan, router mounting, /health
  core/
    config.py                settings and path resolution
    ai_exceptions.py         AI capability error types/helpers
  db/
    session.py               async engine + get_db dependency
    models.py                SQLModel tables
    migrations.py            Alembic runner
  api/
    organize.py              /api/organize + /api/organize/apply
    summary.py               /api/summary + /api/summary/stream
    history.py               /api/history endpoints
    search.py                /api/search/files (mock)
    settings/
      categories.py          category CRUD + batch
      base_path.py           default base path + onboarding
      auto_organize.py       watcher and schedule config
      store.py               app_settings key helpers
  services/
    ai/
      llm_client.py          async HTTP client for llama-server
      rag_service.py         RAG wrapper
      summary_service.py     per-file summary generation
      rename_service.py      rename suggestions
    files/
      scanner_service.py     file metadata + security checks
      docling_parser.py      structured parsing via docling
      text_cache.py          in-memory parse-to-summary bridge
    categories/
      classification_service.py  embedding + cosine scoring
      seed_service.py            default categories + embedding backfill
      category_embedding_text.py embedding text composition
    background_ingest.py     two-phase ingest orchestration
    summary_workflow_service.py cache-aware summary orchestration
    history_service.py       history read/write operations
    system_log_service.py    operational log write/cleanup
    startup_checks.py        DB/LLM/RAG health checks
    voyager_service.py       fastapi-voyager integration
```

## Startup and Shutdown Lifecycle

The FastAPI lifespan in `app/main.py` performs ordered initialization.

Startup sequence:

1. Ensure storage directory exists (`database_path` parent).
2. Run Alembic migrations (`run_migrations`).
3. Cleanup old `system_logs` based on retention config.
4. Start `llm_client` and probe AI availability.
5. Setup `RagService`.
6. Start background ingest worker if RAG is ready.
7. Backfill missing category embeddings when possible.
8. Run startup checks (`database`, `llm`, `rag`) and cache results for `/health`.
9. Persist startup event to `system_logs`.

Shutdown sequence:

1. Stop background ingest worker.
2. Shutdown `llm_client` HTTP client.
3. Persist shutdown event to `system_logs`.

## Core Workflows

### Organize Workflow (`POST /api/organize`)

Request model:

```json
{
  "file_paths": ["/absolute/path/file.pdf"],
  "force": false
}
```

Response model is keyed by input filepath:

```json
{
  "results": {
    "/absolute/path/file.pdf": {
      "file_id": "...",
      "analysis": { "suggested_names": ["..."] },
      "categories": [
        { "category_id": "...", "name": "...", "score": 82.4 }
      ],
      "error": null
    }
  }
}
```

Per-file pipeline (`_process_single_file_inner`):

1. Acquire per-path async lock to prevent concurrent races.
2. Scan file (`ScannerService`) for path validity, readability, hash, metadata.
3. Upsert `files` row by `current_path`.
4. Compute active categories hash for cache invalidation.
5. Branch into one of three cache modes:

   - Full cache hit: file unchanged + categories hash unchanged + `force=false`.
     - Return cached analysis/scores quickly.
     - Log `organized_cached` history.

   - Partial cache reclassify: file unchanged + analysis exists + category semantics changed.
     - Reuse cached summary and suggested names.
     - Recompute category scores only.
     - Update `categories_hash`.
     - Log `organized_reclassified` history.

   - Full processing: new/changed/forced.
     - Validate live AI capabilities (`rag`, `general`, `embedding`).
     - Enqueue ingest (docling parse inline, RAG insert in background queue).
     - Generate summary (`SummaryService`).
     - Generate rename suggestions (`RenameService`).
     - Upsert `file_analysis`.
     - Classify with summary-enriched embedding (`ClassificationService`).
     - Log `organized` history.

6. Convert category cosine scores (0..1) to API percentages (0..100).

Failure behavior:

- Scanner errors return per-file error without crashing request.
- AI capability outages return structured per-file error with current partial state.
- Step-level warnings are persisted to `system_logs` (`organize.pipeline` component).

Data side effects:

- Writes/updates `files`, `file_analysis`, `category_scores`, `history_logs`.
- Enqueues content for background RAG ingestion.
- Does not move or rename files physically.

### Apply Decision Workflow (`POST /api/organize/apply`)

Purpose:

- Record user-confirmed rename/move intent after organize results are shown.

Input fields:

- `file_id`
- optional `selected_name`
- optional `selected_category` (`id`, `name`, `score`)

Flow:

1. Resolve file by ID.
2. Validate selected category exists, active, and has destination path.
3. Normalize selected filename and extension.
4. Compute final path for logging/state.
5. Update `files.current_path` and extension in DB.
6. Write history action:
   - `renamed`
   - `moved`
   - `renamed_moved`
7. Return `{ "success": true }`.

This endpoint updates backend state and audit trail. Actual filesystem mutation is expected to be handled by the desktop layer.

### Summary Workflow (`POST /api/summary`, `POST /api/summary/stream`)

`POST /api/summary`:

1. Build `SummaryWorkflowService`.
2. Ensure general AI availability.
3. For each file:
   - return cached `file_analysis.summary` if available and not forced
   - otherwise generate summary via `SummaryService`
   - persist generated summary back into `file_analysis`
4. Compose a multi-file markdown synthesis with LLM.
5. If compose fails, use deterministic markdown fallback.

`POST /api/summary/stream`:

- Same per-file preparation, then stream synthesis tokens via SSE (`event: chunk`).
- Emits `meta` and `done` events for UI.

`SummaryService` strategy:

- Image extensions: call vision-capable chat path with image base64.
- Other files: consume docling text from `TextCache` when available.
- Fallback to filename-based context if no cached text is available.

### Settings and Onboarding Workflows

#### Categories (`/api/settings/categories`)

- CRUD and batch creation.
- `enabled` and `folder_path` API field names map to DB `is_active` and `destination_path`.
- Category create/update triggers embedding generation through `ClassificationService`.
- List endpoint includes `learning` flag based on category score usage.

#### Default Base Path and Onboarding (`/api/settings/default-base-path`, `/api/settings/onboarding`)

`PUT /default-base-path`:

1. Validate path parent exists.
2. Upsert base path setting.
3. Initialize onboarding status keys in `app_settings`.
4. If categories table is empty, seed default categories.
5. Generate missing embeddings when AI is available.
6. Auto-update destination path for active categories where `is_path_manual=false`.
7. Mark onboarding state as completed.

This single endpoint supports first-run seeding and later base-path updates.

#### Auto Organize Settings (`/api/settings/auto-organize`)

- Global enable/disable master setting persisted in `app_settings`.
- Watched folder CRUD in `watched_folders`.
- Each watcher stores cadence (`frequency_value`, `frequency_unit`, `frequency_seconds`), recursion, next scan time, and error state.

Note: scheduler execution logic is not yet wired into this service layer; routes currently manage persisted configuration.

### History Workflow

`/api/history` provides read models over `history_logs` with enriched metadata.

Capabilities:

- Paginated listing with action/search filters.
- Per-file history retrieval.
- Explicit note history insertion (`POST /api/history/note`).
- Mock UI endpoint (`GET /api/history/list`) for frontend development.

## Database Schema

Current SQLModel tables:

1. `app_settings`
2. `watched_folders`
3. `categories`
4. `files`
5. `file_analysis`
6. `category_scores`
7. `history_logs`
8. `system_logs`

Core relationships:

```text
categories 1 --- * category_scores * --- 1 files
files      1 --- 1 file_analysis
files      1 --- * history_logs
```

Important model details:

- `files.current_path` tracks latest logical path after confirmed actions.
- `file_analysis.suggested_names` stores JSON list of names.
- `file_analysis.categories_hash` stores hash of active category semantics to drive cache invalidation.
- `categories.is_path_manual` protects user-customized destinations from base-path auto-update.
- `watched_folders` has SQL constraints for positive cadence and valid units.

## AI and RAG Integration

### LLM Client (`app/services/ai/llm_client.py`)

`LlmClient` is a singleton async HTTP client for llama-server.

Capabilities:

- General chat (`achat`, `achat_stream`)
- Vision chat with fallback (`achat_with_vision`)
- Embeddings (`aembed`)
- Capability checks (`ensure_general_available`, `ensure_embedding_available`)

### RAG Service (`app/services/ai/rag_service.py`)

`RagService` wraps RAG-Anything initialization and delegates model calls to `LlmClient`.

Exposed operations:

- `setup()`
- `ingest(file_path)`
- `embed_texts(texts)`
- `semantic_search(...)`
- `multimodal_search(...)`

### Background Ingest (`app/services/background_ingest.py`)

Two-phase ingest:

1. Inline parse with `DoclingParser` to produce structured `content_list` and cache extractable text.
2. Queue RAG insertion asynchronously to avoid blocking API response latency.

Queue statuses surfaced to organize pipeline:

- `queued`
- `queue_full`
- `parse_failed`
- `skipped_image`

## Caching and Invalidation Strategy

Organize endpoint uses multi-layer caching:

1. File-change cache via SHA-256 in `files.hash`.
2. Category-semantics cache via `file_analysis.categories_hash`.
3. Summary cache via `file_analysis.summary`.
4. Transient parse-to-summary cache via in-memory `TextCache`.

Invalidation triggers:

- file content hash change
- forced run (`force=true`)
- category name/description changes (hash mismatch)

## Security Model

Path security is enforced in scanner service:

1. Resolve and normalize paths.
2. Reject blocked directories.
3. Enforce allowed roots when configured.
4. Verify existence, file type, and read permission.

Operational constraints:

- API works on absolute local paths.
- Organize is read-only for filesystem side effects.
- No upload flow.
- Services run on localhost.

## Observability and Diagnostics

Diagnostics primitives:

- Structured operational events in `system_logs`.
- Audit trail in `history_logs`.
- Startup checks cached and exposed via `/health`.
- Step-level timing metadata for organize history entries.
- Voyager ER visualization mounted at `/voyager` (configurable).

Voyager integration includes a compatibility patch for `fastapi-voyager` core type handling to avoid crashes on non-class response model shapes.

## Dependency Injection and Service Lifecycles

Lifecycle model:

| Component | Lifecycle | Notes |
|---|---|---|
| `RagService` | singleton | heavy setup during startup |
| `LlmClient` | singleton | startup/shutdown managed in lifespan |
| `BackgroundIngestWorker` | singleton | started when RAG is ready |
| `TextCache` | singleton | process memory bridge for parse/summary |
| `ScannerService` | per request | stateless |
| `ClassificationService` | per request | wraps singleton RAG |
| `SummaryService` | per request | uses singleton LLM client |
| `RenameService` | per request | stateless wrapper over LLM client |
| `HistoryService` | per request | DB utility |
| `SystemLogService` | per request/ad hoc | DB utility |
| `AsyncSession` | per request | commit/rollback in `get_db` |

## API Surface

Current mounted endpoints:

- `GET /health`
- `POST /api/organize`
- `POST /api/organize/apply`
- `POST /api/summary`
- `POST /api/summary/stream`
- `POST /api/search/files` (mock search)
- `GET /api/history`
- `GET /api/history/file/{file_id}`
- `POST /api/history/note`
- `GET /api/history/list` (mock list)
- `GET /api/settings/categories`
- `POST /api/settings/categories`
- `GET /api/settings/categories/{category_id}`
- `PATCH /api/settings/categories/{category_id}`
- `DELETE /api/settings/categories/{category_id}`
- `POST /api/settings/categories/batch`
- `GET /api/settings/default-base-path`
- `PUT /api/settings/default-base-path`
- `GET /api/settings/onboarding`
- `GET /api/settings/auto-organize`
- `PUT /api/settings/auto-organize`
- `GET /api/settings/auto-organize/folders`
- `POST /api/settings/auto-organize/folders`
- `PATCH /api/settings/auto-organize/folders/{watcher_id}`
- `DELETE /api/settings/auto-organize/folders/{watcher_id}`

## Configuration Model

Configuration source: `app/core/config.py` (`KLIN_` env prefix).

Key groups:

- Runtime/server: host, port, debug, CORS.
- Storage: `database_path`, `rag_working_dir`, frozen-vs-dev storage resolution.
- AI server: `llama_server_url`, embedding dimension, token budgets.
- Pipeline tuning: summary and rename token/context limits, classification top-k.
- Logging retention: cleanup policy for `system_logs`.
- Future toggles: watcher polling and embedding cache settings.

Path resolution:

- Dev (source run): `.storage/` under repository root.
- Frozen bundle: `KLIN_APP_DATA_DIR` if provided, else `~/.klin`.

## Current Limits and Planned Extensions

Implemented but not fully automated yet:

- Watched-folder scheduler persistence exists; continuous scheduler execution path is not yet active.
- Search endpoint currently returns mock data.

Potential roadmap areas already represented in code/config:

- richer duplicate detection in `RagService.find_duplicates`
- auto-organize scheduler runtime
- stronger diagnostics surfaces over `system_logs`
