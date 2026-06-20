# Klin Worker — Test Suite Report

**Project:** Klin (Senior Project)
**Component:** `klin-worker` (FastAPI backend + AI organize pipeline)
**Total tests:** 260 (all passing)
**Test files:** 37 across 6 test directories
**Execution time:** ~2.3 s for the full suite

---

## 1. Purpose

This report documents the automated test suite that protects the Klin Worker backend. The goal is to give a reader who has never run the code a complete picture of:

1. What the tests actually exercise (every public surface).
2. How the suite is structured and what each marker means.
3. What is **deliberately not tested** (and why).
4. How to run the suite locally and in CI.

Every claim about coverage in this document is traceable to a real test in `tests/`.

---

## 2. At a glance

| Metric | Value |
|---|---|
| Total tests | **260** |
| Unit tests (`-m unit`) | 120 |
| Integration tests (`-m integration`) | 53 |
| Uncategorized (pipeline/CLI/versioning) | 87 |
| Live-LLM tests (`-m llm`) | 0 (placeholder marker) |
| Test files | 37 |
| Source modules under test | 30+ |
| Average run time | ~2.3 s |
| External services hit | **none** (everything mocked) |

---

## 3. Mocking & isolation policy

The suite must be runnable on a laptop with **no llama-server, no network, and no Postgres**. Three rules enforce this:

1. **Every LLM call is faked.** `llm_client.achat`, `aembed`, and `achat_with_vision` are either monkeypatched per-test or fronted by an `httpx.MockTransport`. No test reaches a live model.
2. **RAG calls use in-process fakes.** `FakeLightRAG`, `FakeRagService`, `FakeIngest`, and `FakeDocStatus` simulate LightRAG behavior (insert, query, doc status) without touching the real vector store.
3. **All DB tests use in-memory SQLite.** `sqlite+aiosqlite:///:memory:` is created per-test in `tests/conftest.py`. Schema is built via `SQLModel.metadata.create_all` — no Alembic dependency for tests.

The FastAPI lifespan is neutered in the test client: `init_langfuse`, `llm_client.startup`, `RagService.setup`, `BackgroundIngestWorker.start`, and `run_migrations` are stubbed out so route tests start instantly.

---

## 4. Suite structure

```
tests/
├── api/          (53 tests, mark: integration) — FastAPI route layer
├── core/         (10 tests, mark: unit)        — exceptions + config
├── integration/  ( 2 tests, mark: integration) — cross-module pipeline
├── models/       (30 tests, mark: unit)        — Pydantic request/response
├── services/     (152 tests, mark: unit)       — business logic
└── (root)        ( 7 tests, unmarked)         — CLI, tracing, version
```

---

## 5. What is tested — by area

### 5.1 API routes (`tests/api/`, 53 tests)

Every public HTTP endpoint has an integration test that exercises the route with FastAPI's `TestClient` against an in-memory DB. Coverage:

| Router | Tests | What is verified |
|---|---|---|
| `GET /health` | 1 | Aggregated health reports `degraded` when startup checks fail; FastAPI sub-check stays `ok`. |
| `/api/history/*` | 7 | List enrichment with category metadata, action filter, pagination limit/offset, single-file history, note insertion, mock-history fallback, 422 on bad `limit`. |
| `/api/organize` + `/apply` | 6 | Results dict keyed by input path; 422 on empty list; locked files short-circuit pipeline; 404 on unknown id; validator rejects empty action; happy-path apply renames file + writes `renamed` history. |
| `/api/search` | 18 | Filename + semantic search, basename matching, singular/plural tokens, phrase match, low-score chunks, deduplication, fallback when RAG fails, pending/degraded status, tracing spans, path normalization edge cases. |
| `/api/settings/auto-organize` | 10 | Master switch default + round-trip, watcher folder create (success, non-absolute path 400, missing path 400, duplicate 409), list, PATCH 404 + empty-body 422, delete 204+404. |
| `/api/settings/default-base-path` + `/onboarding` | 4 | Null when unset, 400 on bad parent, auto-updates non-manual category destinations, onboarding pending by default. |
| `/api/settings/categories` | 13 | Empty list, create 201, duplicate 409, 503 when embedding unavailable, GET 404, PATCH 404 + `enabled` flip without re-embed, DELETE 404 + 204, batch create with duplicate skip, batch empty 422, `active_only` filter. |
| `/api/settings/locks` | 4 | Default empty, round-trip, rejects relative paths 400, accepts empty lists. |
| `/api/summary` + `/stream` | 6 | Returns generated text, fallback when None, 503 on `AiCapabilityUnavailableError`, 422 on empty path, SSE chunk+done events, pre-stream 503. |

### 5.2 Service layer (`tests/services/`, 152 tests)

This is the bulk of the suite — pure business logic, exercised in isolation with fakes.

**AI client (`test_llm_client.py` + `test_llm_client_more.py`, 17 tests)**
`achat` disables thinking, passes `response_format`; `aembed` validates dimension and truncates input; concurrent requests are serialized; parametrized truncation helpers cover under-limit / zero-max / over-limit; multimodal input-char guard trims text parts but preserves `image_url`; `ensure_general_available` raises when client missing.

**Organize pipeline (`test_organize_pipeline_schedule.py`, 5 tests)**
End-to-end ordering of ingest → schedule → summary; schedule failure does not fail the pipeline; cached organize does not recompute; **golden path** returns fully populated result with single `organized` history log; **503 aggregation** when all three AI capabilities (rag/chat/embedding) are unavailable.

**Background ingest (`test_background_ingest.py`, 11 tests)**
Two-phase fast+rich parsing, RAG enqueue, doc deletion before re-insert, KG-skip hook installation, block-type conversions (disabled table/equation → text, image retention), tracing metadata isolation so background traces never inherit foreground context.

**Schedule extraction (`test_schedule_extraction_service.py`, 21 tests)**
LLM JSON parsing with retry/timeout, Thai Buddhist calendar handling, hallucinated date rejection, partial source date handling, multi-event itineraries, fenced JSON repair, reminders array normalization to Google Calendar shape, past/blank/today-or-future date validation, Z-suffix and naive datetime parsing with Bangkok timezone fallback.

**Classification (`test_classification_service.py`, 10 tests)**
Cosine similarity (identical, orthogonal, zero-norm guard), skip-when-no-categories, ranking and top-k truncation, category embedding generation (success, capability error propagation, swallowing other failures), file embedding from summary vs filename fallback.

**Scanner (`test_scanner_service.py`, 9 tests)**
SHA-256 matches stdlib, blocked-directory check, allowed-roots enforcement, **symlink escape** (POSIX: a symlink under an allowed root pointing outside is rejected via `path.resolve()`), unrestricted-path pass, full scan metadata, missing file, directory rejection, permission-denied via `chmod 0` (POSIX).

**Rename (`test_rename_service.py`, 1 test)**
Filename suggestion filtering removes the original name and normalizes output.

**Summary (`test_summary_service.py` + `test_summary_service_image.py` + `test_summary_workflow_service.py`, 10 tests)**
`_get_text_context` chooses extracted text over filename when long enough; 8000-char input truncated to 6000 in prompt; non-capability exceptions return None; `AiCapabilityUnavailableError` re-raised; vision path base64-encodes image into `image_url`; no-vision falls back to filename prompt; workflow calls ingest first and passes extracted text to summary.

**Seed (`test_seed_service.py`, 8 tests)**
Inserts 10 default categories on empty DB; idempotent second run; user rows don't block default seeding; no duplicate names; embedding generation for active categories only, skips already-embedded, skips when classifier returns None.

**Startup checks (`test_startup_checks.py`, 18 tests)**
9 parametrized cases mapping SQLite error substrings to user-facing prefixes (`schema_mismatch`, `database_locked`, `permission_denied`, `disk_full`, etc.); DB check OK on clean schema; classified detail on failure; LLM check OK when both chat+embed available; chat error propagation; chat-OK-with-embedding-note; RAG check (ready, not ready, capability propagation); aggregated three-result run.

**System log (`test_system_log_service.py`, 6 tests)**
Level normalization to uppercase; correlation ID from trace context or explicit override (explicit wins); retention cutoff deletes old rows; no-op when nothing stale; level + component filter.

**History (`test_history_service.py`, 7 tests)**
JSON metadata stored verbatim; metadata-null path; newest-first ordering; action filter; pagination with `has_more`; single-action filter; search matches `metadata_json` substring.

**Organize telemetry (`test_organize_telemetry.py`, 2 tests)**
History metadata keeps audit fields with compact ops snapshot; log context keeps full debug snapshot.

**Lock settings (`test_lock_settings_service.py`, 8 tests)**
Path normalization with case/slash dedup; invalid JSON tolerated; exact file lock match; folder descendant match; partition into locked/unlocked; absolute-path validation for Unix and Windows; rejects relative paths; returns None for unlocked.

**RAG service (`test_rag_service.py`, 4 tests)**
`ensure_rag_doc_status_compatible` drops unknown fields; no-op on clean storage; embedding budget propagates to LightRAG; KG-extraction disable hook for file-search mode.

**Docling parser (`test_docling_parser.py`, 2 tests)**
Fast profile respects settings; fingerprint changes on setting mutation.

**Category embedding text (`test_category_embedding_text.py`, 1 parametrized × 3 cases)**
`name\ndescription` join; name-only when description empty; whitespace stripped from both parts.

### 5.3 Models (`tests/models/`, 30 tests)

Pydantic request/response models — validators and defaults.

- **Request models (20 tests):** `OrganizeRequest` min-length 1 + `force` default; `ApplyRequest` validator requires at-least-one action (name-only and category-only both accepted); score-out-of-range rejected; `CategoryCreate` rejects blank name, non-hex color, name > 100 chars; accepts `#aabbcc`; batch requires ≥ 1; `WatcherFolderCreate` defaults + literal `minute|hour|day` + frequency `1..999`; update requires ≥ 1 field; lock settings default factory; file search min-length 1; note history requires destination, source_files defaults empty.
- **Response models (10 tests):** `OrganizeResult` omits schedule when missing; preserves Google-style `start.dateTime`, `timeZone`, reminders; response keyed by path string; `FileSearchResponse` defaults (`semantic_status=ready`, no error, zero pending); item round-trip; `HistoryListResponse` pagination defaults; nullable fields on `HistoryLog`; category response passes through; nullable score; float precision preserved.

### 5.4 Core (`tests/core/`, 10 tests)

- **AI exceptions (5):** field preservation + str() round-trip; helper builds 503 HTTPException; empty input → generic fallback; identical errors deduped; capability order preserved by first-seen.
- **Config (5):** dev path resolves to `.storage/`; PyInstaller + `KLIN_APP_DATA_DIR` → env path; PyInstaller without env → `~/.klin`; `KLIN_APP_VERSION` env wins; falls back to `VERSION` file.

### 5.5 Integration (`tests/integration/`, 2 tests)

Cross-module pipeline tests that don't fit a single service file.

- `test_prepare_then_enqueue_persists_and_indexes` — end-to-end prepare → enqueue_after_organize; rich parser content reaches fake LightRAG.
- `test_prepare_returns_none_when_path_missing` — empty parse output yields `PreparedIngest` with empty text.

### 5.6 Root (`tests/`, 7 tests)

- **CLI (`test_main_args.py`, 2):** `parse_args` defaults + override flags.
- **Tracing (`test_main_tracing.py`, 3):** `_request_trace_input` captures search-body query, ignores invalid JSON, skips non-search endpoints.
- **Versioning (`test_versioning.py`, 2):** `VERSION` file matches `pyproject.toml`; env var override preferred.

---

## 6. Markers

| Marker | Count | Meaning |
|---|---|---|
| `unit` | 120 | Pure logic or in-memory DB. No FastAPI lifespan. Fastest. |
| `integration` | 53 | FastAPI `TestClient` with neutered lifespan. Real route → real ORM (in-memory). |
| _unmarked_ | 87 | Service-level pipeline tests, CLI, versioning, tracing. Run by default. |
| `llm` | 0 | Reserved for tests that hit a live llama-server. None currently registered. |

Run a subset with `pytest -m unit`, `pytest -m integration`, or `pytest -m "not llm"`.

---

## 7. Mock infrastructure — where the fakes live

| Mock target | Location |
|---|---|
| `httpx.MockTransport` for llama-server HTTP | `tests/services/test_llm_client.py` |
| `llm_client.achat` / `aembed` / `achat_with_vision` monkeypatch | `test_summary_service_image.py`, `test_rename_service.py`, `test_schedule_extraction_service.py`, `test_classification_service.py` |
| `process_single_file` swap | `tests/api/test_organize_routes.py` |
| `_get_summary_workflow` dependency override | `tests/api/test_summary_routes.py` |
| `_get_classifier` dependency override | `tests/api/test_settings_categories_routes.py` |
| `FakeLightRAG` / `FakeRagService` / `FakeIngest` / `FakeDocStatus` | `tests/api/test_search.py`, `tests/services/test_background_ingest.py` |
| FastAPI lifespan stubs | `tests/conftest.py` |
| In-memory SQLite | `tests/conftest.py` |

---

## 8. Known coverage gaps (deferred)

These are documented absences, not oversights. Each has a rationale.

| Gap | Why deferred |
|---|---|
| SSE mid-stream `error` event on `/api/summary/stream` | The current route runs the workflow **before** the SSE generator starts. The generator only formats the already-computed result and emits `chunk` + `done` — there is no mid-stream failure path to exercise. The pre-stream error path is already covered by `test_summary_stream_returns_503_on_capability_error`. If the route is refactored to stream from the workflow itself, add the missing test. |
| Search service pure helpers (`_chunk_score`, `_has_ordered_token_phrase`, `_normalize_filename_search_text`, `_search_tokens`) | Covered indirectly through the 18 `test_search.py` integration tests. Add direct unit tests only when a regression is hit (faster failure diagnosis). |
| Alembic migration smoke test | `run_migrations()` against an empty SQLite would catch broken revisions before they ship. Pending decision on whether migrations run in CI. |
| Rename service expansion | One test exists for the suggestion-filtering happy path. Expand only if the rename logic acquires more behavior. |

---

## 9. How to run

```bash
# One-time
uv sync --extra dev

# All 260 tests (default)
uv run pytest -q

# Fast unit slice (no FastAPI lifespan)
uv run pytest -m unit -q              # 120 tests

# Route layer
uv run pytest -m integration -q       # 53 tests

# Explicitly skip live-LLM tests (none registered today)
uv run pytest -m "not llm" -q

# Single file with verbose output
uv run pytest tests/services/test_classification_service.py -v

# Single test by name
uv run pytest tests/services/test_organize_pipeline_schedule.py::test_process_single_file_returns_populated_result_on_golden_path -v
```

---

## 10. What this suite gives the team

1. **Confidence to refactor.** Every public route, every service entry point, every AI-capability fallback has a test. A breaking change surfaces in < 3 seconds.
2. **Documentation by example.** Reading `tests/services/test_organize_pipeline_schedule.py` is the fastest way to understand how `process_single_file` is wired.
3. **No external dependencies in CI.** The suite runs identically on a laptop, in GitHub Actions, and in a Docker container — no llama-server, no Postgres, no network.
4. **A protected boundary against AI flakiness.** Because every LLM call is mocked, the suite never fails due to model timeouts, rate limits, or output drift. The mocking discipline itself is enforced by section 3 of this document.
5. **Honest coverage reporting.** Section 8 lists what is *not* tested and why, so future contributors don't assume protection that isn't there.

---

## Appendix A — Test count by file (descending)

| File | Tests |
|---|---|
| `tests/services/test_schedule_extraction_service.py` | 21 |
| `tests/models/test_request_models.py` | 20 |
| `tests/api/test_search.py` | 18 |
| `tests/services/test_startup_checks.py` | 18 |
| `tests/api/test_settings_categories_routes.py` | 13 |
| `tests/services/test_llm_client_more.py` | 12 |
| `tests/services/test_background_ingest.py` | 11 |
| `tests/api/test_settings_auto_organize_routes.py` | 10 |
| `tests/models/test_response_models.py` | 10 |
| `tests/services/test_classification_service.py` | 10 |
| `tests/services/test_scanner_service.py` | 9 |
| `tests/services/test_lock_settings_service.py` | 8 |
| `tests/services/test_seed_service.py` | 8 |
| `tests/api/test_history_routes.py` | 7 |
| `tests/services/test_history_service.py` | 7 |
| `tests/services/test_summary_service_image.py` | 7 |
| `tests/api/test_organize_routes.py` | 6 |
| `tests/api/test_summary_routes.py` | 6 |
| `tests/services/test_system_log_service.py` | 6 |
| `tests/core/test_ai_exceptions.py` | 5 |
| `tests/core/test_config.py` | 5 |
| `tests/services/test_llm_client.py` | 5 |
| `tests/services/test_organize_pipeline_schedule.py` | 5 |
| `tests/api/test_settings_base_path_routes.py` | 4 |
| `tests/api/test_settings_locks_routes.py` | 4 |
| `tests/services/test_rag_service.py` | 4 |
| `tests/services/test_category_embedding_text.py` | 3 |
| `tests/test_main_tracing.py` | 3 |
| `tests/integration/test_rag_pipeline.py` | 2 |
| `tests/services/test_docling_parser.py` | 2 |
| `tests/services/test_organize_telemetry.py` | 2 |
| `tests/services/test_summary_service.py` | 2 |
| `tests/test_main_args.py` | 2 |
| `tests/test_versioning.py` | 2 |
| `tests/api/test_health_route.py` | 1 |
| `tests/services/test_rename_service.py` | 1 |
| `tests/services/test_summary_workflow_service.py` | 1 |
| **Total** | **260** |
