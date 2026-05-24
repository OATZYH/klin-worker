# Klin Worker — Test Run Results

**Run date:** 2026-05-24 18:02 +07
**Branch:** `test/add-test`
**Runner:** pytest 9.0.2 on Python 3.13.9 (uv-managed venv)
**Command:** `uv run pytest -q`

---

## Headline result

```
260 passed, 178 warnings in 2.35s
```

**All 260 tests pass. Zero failures. Zero errors. Zero skips on this run.**

> Warnings are SQLAlchemy/SQLModel `session.execute` deprecation notices in
> production code (`organize_pipeline.py`, `search_service.py`). They do not
> affect test outcomes and are tracked separately.

---

## Results by marker

| Marker | Run command | Result | Time |
|---|---|---|---|
| (all) | `uv run pytest -q` | **260 passed** | 2.35 s |
| `unit` | `uv run pytest -m unit -q` | **120 passed, 140 deselected** | 0.78 s |
| `integration` | `uv run pytest -m integration -q` | **53 passed, 207 deselected** | 1.81 s |
| `llm` | `uv run pytest -m llm -q` | **0 collected** (no live-LLM tests registered) | — |

---

## Results by directory

| Directory | Tests | Result | Time |
|---|---|---|---|
| `tests/api/` | 69 | **69 passed** | 1.80 s |
| `tests/core/` | 10 | **10 passed** | 0.11 s |
| `tests/integration/` | 2 | **2 passed** | 0.38 s |
| `tests/models/` | 30 | **30 passed** | 0.04 s |
| `tests/services/` | 142 | **142 passed** | 0.78 s |
| `tests/` (root: CLI/tracing/version) | 7 | **7 passed** | 0.53 s |
| **Total** | **260** | **260 passed** | — |

(Per-directory totals add to 260; per-directory wall times do not sum to the full-suite time because each pytest invocation pays its own startup cost.)

---

## Results by file

Every file ran clean. Sorted by test count, descending.

| File | Tests | Result | Time |
|---|---|---|---|
| `tests/services/test_schedule_extraction_service.py` | 21 | passed | 0.32 s |
| `tests/models/test_request_models.py` | 20 | passed | 0.03 s |
| `tests/api/test_search.py` | 18 | passed | 0.85 s |
| `tests/services/test_startup_checks.py` | 18 | passed | 0.30 s |
| `tests/api/test_settings_categories_routes.py` | 13 | passed | 0.81 s |
| `tests/services/test_llm_client_more.py` | 12 | passed | 0.10 s |
| `tests/services/test_background_ingest.py` | 11 | passed | 0.14 s |
| `tests/api/test_settings_auto_organize_routes.py` | 10 | passed | 0.72 s |
| `tests/models/test_response_models.py` | 10 | passed | 0.03 s |
| `tests/services/test_classification_service.py` | 10 | passed | 0.31 s |
| `tests/services/test_scanner_service.py` | 9 | passed | 0.22 s |
| `tests/services/test_lock_settings_service.py` | 8 | passed | 0.10 s |
| `tests/services/test_seed_service.py` | 8 | passed | 0.12 s |
| `tests/api/test_history_routes.py` | 7 | passed | 0.70 s |
| `tests/services/test_history_service.py` | 7 | passed | 0.28 s |
| `tests/services/test_summary_service_image.py` | 7 | passed | 0.10 s |
| `tests/api/test_organize_routes.py` | 6 | passed | 0.63 s |
| `tests/api/test_summary_routes.py` | 6 | passed | 0.64 s |
| `tests/services/test_system_log_service.py` | 6 | passed | 0.26 s |
| `tests/core/test_ai_exceptions.py` | 5 | passed | 0.05 s |
| `tests/core/test_config.py` | 5 | passed | 0.06 s |
| `tests/services/test_llm_client.py` | 5 | passed | 0.10 s |
| `tests/services/test_organize_pipeline_schedule.py` | 5 | passed | 0.48 s |
| `tests/api/test_settings_base_path_routes.py` | 4 | passed | 0.60 s |
| `tests/api/test_settings_locks_routes.py` | 4 | passed | 0.60 s |
| `tests/services/test_rag_service.py` | 4 | passed | 0.10 s |
| `tests/services/test_category_embedding_text.py` | 3 | passed | 0.04 s |
| `tests/test_main_tracing.py` | 3 | passed | 0.20 s |
| `tests/integration/test_rag_pipeline.py` | 2 | passed | 0.38 s |
| `tests/services/test_docling_parser.py` | 2 | passed | 0.05 s |
| `tests/services/test_organize_telemetry.py` | 2 | passed | 0.10 s |
| `tests/services/test_summary_service.py` | 2 | passed | 0.05 s |
| `tests/test_main_args.py` | 2 | passed | 0.18 s |
| `tests/test_versioning.py` | 2 | passed | 0.20 s |
| `tests/api/test_health_route.py` | 1 | passed | 0.54 s |
| `tests/services/test_rename_service.py` | 1 | passed | 0.05 s |
| `tests/services/test_summary_workflow_service.py` | 1 | passed | 0.05 s |
| **Total** | **260** | **260 passed** | — |

---

## Results by functional area

Mapped to the sections of `test-report.md` so a reader can verify each claim is backed by a green test run.

### API routes — 69 / 69 passed

| Endpoint area | File | Tests | Result |
|---|---|---|---|
| Health | `test_health_route.py` | 1 | passed |
| History | `test_history_routes.py` | 7 | passed |
| Organize + apply | `test_organize_routes.py` | 6 | passed |
| Search | `test_search.py` | 18 | passed |
| Auto-organize settings | `test_settings_auto_organize_routes.py` | 10 | passed |
| Base path settings | `test_settings_base_path_routes.py` | 4 | passed |
| Categories settings | `test_settings_categories_routes.py` | 13 | passed |
| Locks settings | `test_settings_locks_routes.py` | 4 | passed |
| Summary + stream | `test_summary_routes.py` | 6 | passed |

### Services — 142 / 142 passed

| Service | File | Tests | Result |
|---|---|---|---|
| Background ingest | `test_background_ingest.py` | 11 | passed |
| Category embedding text | `test_category_embedding_text.py` | 3 | passed |
| Classification | `test_classification_service.py` | 10 | passed |
| Docling parser | `test_docling_parser.py` | 2 | passed |
| History | `test_history_service.py` | 7 | passed |
| LLM client | `test_llm_client.py` | 5 | passed |
| LLM client helpers | `test_llm_client_more.py` | 12 | passed |
| Lock settings | `test_lock_settings_service.py` | 8 | passed |
| Organize pipeline (schedule + golden + 503) | `test_organize_pipeline_schedule.py` | 5 | passed |
| Organize telemetry | `test_organize_telemetry.py` | 2 | passed |
| RAG service | `test_rag_service.py` | 4 | passed |
| Rename | `test_rename_service.py` | 1 | passed |
| Scanner (incl. symlink-escape) | `test_scanner_service.py` | 9 | passed |
| Schedule extraction | `test_schedule_extraction_service.py` | 21 | passed |
| Seed | `test_seed_service.py` | 8 | passed |
| Startup checks | `test_startup_checks.py` | 18 | passed |
| Summary | `test_summary_service.py` | 2 | passed |
| Summary (image/vision) | `test_summary_service_image.py` | 7 | passed |
| Summary workflow | `test_summary_workflow_service.py` | 1 | passed |
| System log | `test_system_log_service.py` | 6 | passed |

### Models — 30 / 30 passed

| Layer | File | Tests | Result |
|---|---|---|---|
| Request models (validators + defaults) | `test_request_models.py` | 20 | passed |
| Response models (serialization + defaults) | `test_response_models.py` | 10 | passed |

### Core — 10 / 10 passed

| Module | File | Tests | Result |
|---|---|---|---|
| AI exceptions (503 helper + formatter) | `test_ai_exceptions.py` | 5 | passed |
| Config (storage dir + version resolution) | `test_config.py` | 5 | passed |

### Integration — 2 / 2 passed

| Scope | File | Tests | Result |
|---|---|---|---|
| RAG pipeline (prepare → enqueue) | `test_rag_pipeline.py` | 2 | passed |

### Root (CLI / tracing / versioning) — 7 / 7 passed

| Concern | File | Tests | Result |
|---|---|---|---|
| CLI argument parsing | `test_main_args.py` | 2 | passed |
| Request tracing input capture | `test_main_tracing.py` | 3 | passed |
| Versioning (VERSION ↔ pyproject) | `test_versioning.py` | 2 | passed |

---

## Failure / error / skip summary

| Outcome | Count |
|---|---|
| **passed** | 260 |
| **failed** | 0 |
| **errored** | 0 |
| **skipped** | 0 |
| **deselected** (when running `-m unit` or `-m integration`) | varies by marker |
| **warnings** | 178 (deprecation, non-blocking) |

---

## Warning breakdown

All 178 warnings originate from production code, not the tests themselves. They are SQLModel's recommendation to migrate from `session.execute(select(...))` to `session.exec(select(...))`. Two files account for nearly all of them:

- `app/services/organize/organize_pipeline.py:45` and similar call sites
- `app/services/search_service.py` similar call sites

**Impact on this report:** none — every test passes. The migration to `session.exec()` is a separate cleanup tracked outside this test report.

---

## Reproducibility

```bash
# From repo root
uv sync --extra dev
uv run pytest -q                  # 260 passed, ~2.3 s

# By marker
uv run pytest -m unit -q          # 120 passed
uv run pytest -m integration -q   # 53 passed

# By directory
uv run pytest tests/api -q        # 69 passed
uv run pytest tests/services -q   # 142 passed
uv run pytest tests/models -q     # 30 passed
uv run pytest tests/core -q       # 10 passed
uv run pytest tests/integration -q # 2 passed
```

---

## Conclusion

The Klin Worker backend test suite is **fully green** on the `test/add-test` branch as of 2026-05-24. All 260 tests across 37 files in 6 directories pass. No live external dependencies were needed for the run. Every functional area documented in `test-report.md` has a corresponding green-test row in this report.
