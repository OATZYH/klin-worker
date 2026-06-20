# Developer Onboarding: Overview

This guide is for engineers who need to become productive in klin-worker quickly.

klin-worker is a local FastAPI sidecar used by the desktop app. It receives absolute file paths, runs AI analysis locally, stores metadata in SQLite, and returns organize/summary results.

## First Day Checklist

1. Install prerequisites.
2. Start the API locally.
3. Verify health and dependencies.
4. Seed default categories.
5. Run a smoke request for organize.
6. Learn where each feature lives in the codebase.

## Prerequisites

1. Python 3.13+
2. uv
3. A running local llama-server endpoint (OpenAI-compatible)

Default AI server URL in this project is `http://127.0.0.1:8080/`.

## Quick Start (Local Dev)

From repository root:

```bash
uv sync
uv run fastapi dev app/main.py
```

In another terminal, verify startup:

```bash
curl -s http://127.0.0.1:8000/health | jq
```

Expected shape:

- `status` is `ok` or `degraded`
- `services` contains checks for Database, LLM Server, and RAG-Anything

## First-Run Setup (Seed Categories)

Categories are seeded via base-path setup, not at raw process boot.

Run once on an empty DB:

```bash
curl -X PUT http://127.0.0.1:8000/api/settings/default-base-path \
  -H 'Content-Type: application/json' \
  -d '{"default_base_path":"/Users/you/KlinFiles"}'
```

Then verify categories:

```bash
curl -s http://127.0.0.1:8000/api/settings/categories | jq 'length'
```

## Smoke Test Organize

```bash
curl -X POST http://127.0.0.1:8000/api/organize \
  -H 'Content-Type: application/json' \
  -d '{"file_paths":["/absolute/path/to/file.pdf"],"force":false}' | jq
```

Important:

- Organize does not move or rename files on disk.
- It returns analysis and category scores.
- User-confirmed actions are tracked via `POST /api/organize/apply`.

## Mental Model

High-level request flow:

```text
API request
  -> scanner (metadata + security)
  -> cache decision (full hit / partial reclassify / full run)
  -> background ingest queue (docling parse + RAG insert)
  -> summary + rename
  -> classification scoring
  -> history + response
```

Runtime components:

1. FastAPI (app/main.py)
2. SQLite (SQLModel + Alembic)
3. llama-server HTTP client (chat + embeddings)
4. RAG-Anything wrapper (RagService)
5. Background ingest worker (docling parse + queue)

## Where To Start Reading Code

1. `app/main.py` (startup/shutdown and router wiring)
2. `app/api/organize.py` (primary business workflow)
3. `app/services/background_ingest.py` (parse/ingest behavior)
4. `app/services/ai/llm_client.py` (AI availability and requests)
5. `app/db/models.py` (data model)

## Next Guides

1. [02-rag-concepts.md](./02-rag-concepts.md)
2. [03-file-walkthrough.md](./03-file-walkthrough.md)
3. [04-services.md](./04-services.md)
4. [05-api-routes.md](./05-api-routes.md)
5. [06-organize-pipeline.md](./06-organize-pipeline.md)
