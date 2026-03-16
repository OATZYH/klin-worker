# Service Layer Guide

This is the fastest way to understand what each service owns and when it runs.

## Lifecycle Overview

### Singleton-ish runtime services (created in `app/main.py`)

1. `RagService`
2. `TextCache`
3. `DoclingParser`
4. `BackgroundIngestWorker`
5. `LlmClient` singleton module object

### Per-request services (via FastAPI dependencies)

1. `ScannerService`
2. `ClassificationService`
3. `SummaryService`
4. `RenameService`
5. `HistoryService`
6. `SystemLogService`
7. `AsyncSession`

## Service Responsibilities

## `ScannerService`

File metadata and path security checks.

- Validates path access
- Computes SHA-256
- Returns structured scan result without throwing in normal error cases

## `BackgroundIngestWorker`

Two-phase ingest orchestrator.

1. Inline parse via docling
2. Queue insertion into RAG in background

Returns status flags (`queued`, `queue_full`, `parse_failed`, `skipped_image`) consumed by organize pipeline.

## `DoclingParser`

Structured parser for document formats, run in thread pool to avoid blocking event loop.

Produces RAG-compatible `content_list` and extracted text used by summaries.

## `TextCache`

In-memory bridge between parse and summary phases.

Set by ingest step, consumed by summary step.

## `LlmClient`

Async HTTP wrapper over llama-server.

- capability checks
- chat completion
- streaming
- vision fallback
- embedding requests

## `RagService`

RAG-Anything wrapper and gateway for semantic/embedding operations.

- setup
- ingest
- semantic search
- multimodal search
- embed texts

## `SummaryService`

Single-file summarization strategy.

- image path -> vision summarization
- text/doc path -> context from `TextCache`
- fallback when extracted text unavailable

## `RenameService`

Generates multiple filename suggestions from summary context.

## `ClassificationService`

Category scoring using embeddings and cosine similarity.

- load active category embeddings
- compute similarity
- persist top-k scores

## `SummaryWorkflowService`

Orchestrates multi-file summary endpoint behavior.

- cache-aware per-file summary generation
- persistence into `file_analysis`
- used by both sync and streaming summary routes

## `HistoryService`

Read/write helper for history logs.

## `SystemLogService`

Structured operational logging in DB and cleanup of old records.

## `StartupChecks`

Runs checks for:

1. Database
2. LLM server
3. RAG readiness

Results feed `/health` response.

## Extension Rules of Thumb

1. Put orchestration logic in API layer only when it is request-specific.
2. Keep reusable logic in services.
3. Keep schema changes in `app/db/models.py` and add Alembic migration.
4. Keep AI capability failures explicit with `AiCapabilityUnavailableError`.

Continue with [05-api-routes.md](./05-api-routes.md).
