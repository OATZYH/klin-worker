# Test Suite Index

260 tests across 25 files. Run: `uv run pytest -m "not llm" -q`.

- **Mocking policy**: every LLM call (`llm_client.achat`, `achat_with_vision`, `aembed`) is monkeypatched or fronted by `httpx.MockTransport`. No test reaches a live llama-server. RAG calls use fake LightRAG/RagService objects. DB tests use in-memory `sqlite+aiosqlite:///:memory:`.
- **Markers**: `unit` (120) — pure logic / in-memory DB. `integration` (53) — FastAPI `TestClient` with neutered lifespan. `llm` — pre-existing marker for live-LLM tests (none currently registered). Pipeline tests in `test_organize_pipeline_schedule.py` are uncategorized.

---

## tests/api/ — FastAPI route integration

### test_health_route.py
| Test | What it covers |
|---|---|
| `test_health_returns_degraded_when_startup_checks_fail` | `/health` reports `degraded` when lifespan startup checks fail; `FastAPI` sub-check stays `ok`. |

### test_history_routes.py
| Test | What it covers |
|---|---|
| `test_list_history_returns_user_actions_only` | `GET /api/history` enriches log with category metadata; filters by USER_ACTIONS. |
| `test_list_history_filters_by_action` | `?action=moved` returns only matching action rows. |
| `test_list_history_pagination_respects_limit_param` | `limit`/`offset` query params honored in response body. |
| `test_file_history_endpoint` | `GET /api/history/file/{id}` returns logs for that file. |
| `test_create_note_history_inserts_log` | `POST /api/history/note` creates File row if missing + writes `note` action. |
| `test_mock_history_list` | `GET /api/history/list` serves MOCK_HISTORY_ITEMS. |
| `test_list_history_rejects_invalid_limit` | 422 when `limit=0` or above max (500). |

### test_organize_routes.py
| Test | What it covers |
|---|---|
| `test_organize_returns_results_keyed_by_filepath` | `POST /api/organize` results dict keyed by input path; `process_single_file` stubbed. |
| `test_organize_rejects_empty_file_paths` | 422 on empty `file_paths`. |
| `test_organize_locked_file_short_circuits_pipeline` | Locked file is not sent through `process_single_file`; result carries lock reason. |
| `test_apply_organize_decision_404_when_file_missing` | `POST /api/organize/apply` returns 404 for unknown `file_id`. |
| `test_apply_organize_decision_422_when_no_action_provided` | Pydantic validator rejects payload with neither name nor category. |
| `test_apply_organize_decision_renames_and_logs_history` | End-to-end: File row's `current_path` updated; HistoryLog `renamed` action written. |

### test_search.py (pre-existing)
18 tests covering filename + semantic search, chunk filtering, score thresholds, deduplication, fallback behaviour, pending/degraded status, tracing spans. See file header for individual descriptions.

### test_settings_auto_organize_routes.py
| Test | What it covers |
|---|---|
| `test_master_switch_defaults_to_false` | `GET /api/settings/auto-organize` defaults to `enabled=false`. |
| `test_master_switch_update_round_trip` | `PUT` persists state; subsequent `GET` returns it. |
| `test_create_watcher_folder_success` | 201 + normalized path + `frequency_seconds` derived from unit/value. |
| `test_create_watcher_folder_rejects_non_absolute_path` | 400 on relative path. |
| `test_create_watcher_folder_rejects_missing_path` | 400 when folder does not exist. |
| `test_create_watcher_folder_409_on_duplicate` | 409 when same folder is added twice. |
| `test_list_watcher_folders` | `GET /folders` returns all rows. |
| `test_update_watcher_folder_404_when_missing` | `PATCH` on missing id returns 404. |
| `test_update_watcher_folder_requires_at_least_one_field` | 422 on empty body (validator). |
| `test_delete_watcher_folder_round_trip` | 204 on first delete, 404 on second. |

### test_settings_base_path_routes.py
| Test | What it covers |
|---|---|
| `test_get_default_base_path_returns_none_when_unset` | `GET /default-base-path` returns `null` when nothing stored. |
| `test_put_default_base_path_rejects_nonexistent_parent` | 400 when parent dir doesn't exist. |
| `test_put_default_base_path_persists_and_auto_updates_categories` | Sets path AND rewrites non-manual categories' `destination_path`. |
| `test_onboarding_status_pending_by_default` | `GET /onboarding` returns `pending` + `should_seed_defaults=true`. |

### test_settings_categories_routes.py
| Test | What it covers |
|---|---|
| `test_list_categories_empty` | `GET /categories` returns `[]` on empty DB. |
| `test_create_category_201` | `POST` returns 201; row visible in subsequent `GET`. |
| `test_create_category_409_on_duplicate` | Duplicate name → 409. |
| `test_create_category_503_when_embedding_unavailable` | `AiCapabilityUnavailableError` from classifier → 503. |
| `test_get_category_404` | Unknown id → 404. |
| `test_get_category_returns_existing` | Round-trip create → get. |
| `test_patch_category_404` | `PATCH` missing id → 404. |
| `test_patch_category_updates_without_reembed` | `enabled` flip succeeds without invoking embedding. |
| `test_delete_category_404` | `DELETE` missing id → 404. |
| `test_delete_category_204` | Round-trip create → delete. |
| `test_batch_create_categories_skips_duplicates` | `POST /batch` skips duplicate names, keeps new ones. |
| `test_batch_create_rejects_empty_list` | 422 on empty `categories`. |
| `test_list_categories_active_only_filter` | `?active_only=false` includes disabled rows. |

### test_settings_locks_routes.py
| Test | What it covers |
|---|---|
| `test_get_locks_defaults_to_empty` | `GET /locks` returns empty lists + status rows. |
| `test_put_locks_round_trip` | `PUT` persists; `GET` returns same payload. |
| `test_put_locks_rejects_relative_paths` | 400 when `lock_file` has a relative path. |
| `test_put_locks_accepts_empty_lists` | 200 when both lists are empty. |

### test_summary_routes.py
| Test | What it covers |
|---|---|
| `test_summary_returns_generated_text` | `POST /api/summary` returns workflow's text + `processing_time_ms`. |
| `test_summary_uses_fallback_text_when_workflow_returns_none` | Fallback message used when workflow returns None. |
| `test_summary_returns_503_on_ai_capability_unavailable` | `AiCapabilityUnavailableError` → 503. |
| `test_summary_rejects_empty_file_path` | 422 on empty `file_path`. |
| `test_summary_stream_emits_chunk_and_done_events` | SSE stream emits `chunk` + `done` events with correct JSON shape. |
| `test_summary_stream_returns_503_on_capability_error` | Capability error before SSE stream → 503. |

---

## tests/core/

### test_ai_exceptions.py
| Test | What it covers |
|---|---|
| `test_ai_capability_unavailable_error_preserves_fields` | `.capability` + `.detail` + str() round-trip. |
| `test_to_service_unavailable_http_exception` | Helper builds 503 HTTPException with same detail. |
| `test_format_ai_capability_errors_empty_iterable_returns_default` | Empty input → generic fallback message. |
| `test_format_ai_capability_errors_dedups_identical_errors` | Identical errors collapsed to one detail. |
| `test_format_ai_capability_errors_preserves_capability_order` | Capabilities listed in first-seen order. |

### test_config.py
| Test | What it covers |
|---|---|
| `test_resolve_default_storage_dir_dev_uses_project_storage` | Plain Python → `.storage/` next to project. |
| `test_resolve_default_storage_dir_frozen_uses_env` | PyInstaller + `KLIN_APP_DATA_DIR` set → env path. |
| `test_resolve_default_storage_dir_frozen_falls_back_to_home` | PyInstaller + no env → `~/.klin`. |
| `test_resolve_app_version_prefers_env_var` | `KLIN_APP_VERSION` wins over file. |
| `test_resolve_app_version_falls_back_to_version_file` | Reads project `VERSION` file. |

---

## tests/integration/

### test_rag_pipeline.py
| Test | What it covers |
|---|---|
| `test_prepare_then_enqueue_persists_and_indexes` | End-to-end `prepare → enqueue_after_organize`; rich parser content reaches fake LightRAG. |
| `test_prepare_returns_none_when_path_missing` | Empty parse output yields PreparedIngest with empty text. |

---

## tests/models/

### test_request_models.py
| Test | What it covers |
|---|---|
| `test_organize_request_rejects_empty_file_paths` | `min_length=1` enforced. |
| `test_organize_request_force_defaults_to_false` | `force` default. |
| `test_apply_request_requires_at_least_one_action` | Validator: name or category required. |
| `test_apply_request_accepts_only_selected_name` | Single field is enough. |
| `test_apply_request_accepts_only_selected_category` | Category-only also accepted. |
| `test_apply_selected_category_score_out_of_range` | Score > 100 rejected. |
| `test_category_create_rejects_blank_name` | Empty `name` rejected. |
| `test_category_create_rejects_invalid_color` | Non-hex color rejected. |
| `test_category_create_accepts_hex_color` | `#aabbcc` accepted. |
| `test_category_create_rejects_name_too_long` | `max_length=100` enforced. |
| `test_batch_category_create_requires_at_least_one` | Empty list rejected. |
| `test_watcher_folder_create_defaults` | Default unit/value/recursive flags. |
| `test_watcher_folder_create_rejects_bad_unit` | Literal `minute|hour|day` enforced. |
| `test_watcher_folder_create_rejects_frequency_out_of_range` | `ge=1, le=999` enforced. |
| `test_watcher_folder_update_requires_at_least_one_field` | Validator rejects empty body. |
| `test_watcher_folder_update_accepts_single_field` | Partial update OK. |
| `test_lock_settings_update_defaults_to_empty_lists` | `default_factory=list` applied. |
| `test_file_search_request_rejects_empty_query` | `min_length=1`. |
| `test_note_history_request_requires_destination_path` | Empty `destination_path` rejected. |
| `test_note_history_request_source_files_defaults_empty` | Default empty list. |

### test_response_models.py
| Test | What it covers |
|---|---|
| `test_organize_result_omits_schedule_when_missing` | `exclude_if` drops null `schedule`. |
| `test_organize_result_preserves_google_style_schedule_fields` | `start.dateTime`, `timeZone`, reminders round-trip. |
| `test_organize_response_keys_results_by_file_path` | Dict-typed `results` keyed by path string. |
| `test_file_search_response_defaults` | Defaults: `semantic_status=ready`, no error, zero pending. |
| `test_file_search_result_item_round_trip` | `FileSearchResultItem` serialization. |
| `test_history_list_response_has_more_default_false` | Pagination defaults. |
| `test_history_log_response_accepts_minimal_payload` | Optional fields nullable. |
| `test_category_response_passes_through_fields` | All response fields preserved. |
| `test_selected_category_score_allows_null_score` | Score nullable. |
| `test_category_score_response_keeps_float_score` | Float precision preserved. |

---

## tests/services/

### test_background_ingest.py (pre-existing, 11 tests)
Covers `BackgroundIngestWorker` two-phase fast+rich parsing, RAG enqueue, doc deletion, KG-skip hook, block-type conversions (table / equation / image), tracing metadata isolation.

### test_category_embedding_text.py
| Test | What it covers |
|---|---|
| `test_build_category_embedding_text` (parametrized × 3) | `name\ndescription` join, name-only when description empty, whitespace stripped from both parts. |

### test_classification_service.py
| Test | What it covers |
|---|---|
| `test_cosine_similarity_identical_vectors_returns_one` | Pure math. |
| `test_cosine_similarity_orthogonal_vectors_returns_zero` | Orthogonality. |
| `test_cosine_similarity_zero_norm_returns_zero` | Zero-vector guard. |
| `test_classify_with_embedding_skips_when_no_categories` | Empty category set → `[]`. |
| `test_classify_with_embedding_ranks_and_truncates` | Sorted desc; `classification_top_k` enforced. |
| `test_generate_category_embedding_returns_vector` | Returns list from fake RAG vector. |
| `test_generate_category_embedding_propagates_capability_error` | Re-raises `AiCapabilityUnavailableError`. |
| `test_generate_category_embedding_returns_none_on_other_failure` | Swallows other exceptions → None. |
| `test_get_file_embedding_uses_summary_when_provided` | Embedded text = `"<stem> <suffix>. <summary>"`. |
| `test_get_file_embedding_falls_back_to_filename_only` | Without summary, text = `"<stem> <suffix>"`. |

### test_docling_parser.py (pre-existing, 2 tests)
Fast-profile config respects settings; profile fingerprint changes on setting mutation.

### test_history_service.py
| Test | What it covers |
|---|---|
| `test_log_inserts_row_with_metadata` | JSON metadata stored verbatim. |
| `test_log_omits_metadata_when_none` | `metadata_json` is null. |
| `test_get_by_file_returns_newest_first` | Descending order. |
| `test_get_by_file_filters_by_actions` | Filter by action list. |
| `test_get_recent_page_pagination_and_has_more` | `has_more` flag set correctly across pages. |
| `test_get_recent_page_filters_by_single_action` | Single-action filter. |
| `test_get_recent_page_search_matches_metadata` | `search` matches `metadata_json` content. |

### test_llm_client.py (pre-existing, 5 tests)
`achat` disables thinking, passes `response_format`; `aembed` validates dim + truncates input; concurrent request pipelining.

### test_llm_client_more.py
| Test | What it covers |
|---|---|
| `test_truncate_text_value` (parametrized × 4) | Under-limit unchanged; zero/negative `max_chars` returns input; over-limit truncated to `max_chars`. |
| `test_truncate_embedding_text_value` (parametrized × 3) | Word-budget truncation; long single word capped by `max_tokens*4` chars; empty input unchanged. |
| `test_apply_input_char_guard_trims_string_content` | String-content message trimmed. |
| `test_apply_input_char_guard_trims_text_parts_in_list_content` | Multimodal: text parts trimmed, `image_url` untouched. |
| `test_build_chat_request_body_includes_response_format_when_provided` | JSON-schema response format flows through; `chat_template_kwargs` disables thinking; no `stream` key when not requested. |
| `test_chat_template_kwargs_disables_thinking` | Always disables thinking. |
| `test_ensure_general_available_raises_when_client_missing` | Raises `AiCapabilityUnavailableError`. |

### test_lock_settings_service.py (pre-existing, 8 tests)
Path normalization, dedup, JSON decode robustness, file/folder lock matching, absolute-path validation.

### test_organize_pipeline_schedule.py (5 tests)
Full organize pipeline ordering (ingest → schedule → summary), schedule failure tolerance, cache reuse, golden-path populated result with single `organized` history log, and 503-aggregation when all AI capabilities (rag / chat / embedding) are unavailable.

### test_organize_telemetry.py (pre-existing, 2 tests)
History metadata + log-context snapshots.

### test_rag_service.py (pre-existing, 4 tests)
`ensure_rag_doc_status_compatible` field sanitization, RAG setup config propagation, KG-extraction disable hook.

### test_rename_service.py (pre-existing, 1 test)
Filename suggestion filtering + normalization.

### test_scanner_service.py
| Test | What it covers |
|---|---|
| `test_hash_file_matches_sha256` | sha256 digest of file matches stdlib computation. |
| `test_check_security_blocks_system_directories` | Blocked-dir prefix check. |
| `test_check_security_enforces_allowed_roots` | Allowed-root enforcement. |
| `test_check_security_passes_for_unrestricted_path` | No restrictions configured → pass. |
| `test_check_security_blocks_symlink_pointing_outside_allowed_root` | POSIX-only; symlink under allowed root pointing outside resolves and is rejected. |
| `test_scan_returns_metadata_for_existing_file` | Size + ext + sha256 returned. |
| `test_scan_returns_error_for_missing_file` | Sets `error="File does not exist."` |
| `test_scan_rejects_directory_path` | Sets `error="Path is not a regular file."` |
| `test_scan_reports_permission_denied` | POSIX-only; chmod 0 file → `"Permission denied."` |

### test_schedule_extraction_service.py (pre-existing, 20 tests)
LLM JSON parsing + date validation + retry/timeout handling; covers Thai Buddhist calendar, hallucinated dates, partial source dates, multi-event itineraries.

### test_seed_service.py
| Test | What it covers |
|---|---|
| `test_seed_default_categories_inserts_on_empty_db` | All 10 defaults seeded on empty table. |
| `test_seed_default_categories_skips_when_defaults_present` | Idempotent second run. |
| `test_seed_default_categories_seeds_only_missing_when_user_rows_exist` | User categories don't block default seeding. |
| `test_seed_default_categories_does_not_duplicate_names` | Skips name collisions. |
| `test_generate_missing_embeddings_inserts_for_active_categories` | Writes JSON embedding string. |
| `test_generate_missing_embeddings_skips_already_embedded` | No work if already populated. |
| `test_generate_missing_embeddings_skips_inactive_categories` | Only `is_active=True` rows processed. |
| `test_generate_missing_embeddings_skips_when_classifier_returns_none` | Null vector → no write. |

### test_startup_checks.py
| Test | What it covers |
|---|---|
| `test_classify_db_error_maps_known_substrings` (parametrized × 9) | Maps SQLite error strings to user-facing prefixes (`schema_mismatch`, `database_locked`, `permission_denied`, `disk_full`, etc.). |
| `test_check_database_ok_on_clean_schema` | Reads from real in-memory DB. |
| `test_check_database_failure_returns_classified_detail` | Failed db.execute classified. |
| `test_check_llm_server_ok_when_chat_and_embed_available` | Both probes OK → ready. |
| `test_check_llm_server_returns_fail_when_chat_unreachable` | Chat capability error propagated. |
| `test_check_llm_server_ok_but_notes_embedding_unavailable` | Chat OK + embed fail → ok with note. |
| `test_check_rag_returns_ok_when_ready` | `rag.is_ready` True. |
| `test_check_rag_returns_fail_when_not_ready` | `rag.is_ready` False. |
| `test_check_rag_propagates_capability_detail` | `ensure_ready` raising capability error. |
| `test_run_all_checks_aggregates_three_results` | DB + LLM + RAG combined. |

### test_summary_service.py (pre-existing, 2 tests)
`_get_text_context` text-vs-filename fallback.

### test_summary_service_image.py
| Test | What it covers |
|---|---|
| `test_summarise_text_truncates_long_extracted_text` | 8000-char input truncated to 6000 in prompt. |
| `test_summarise_returns_none_on_unrelated_exception` | Non-capability exceptions return None. |
| `test_summarise_propagates_capability_error` | `AiCapabilityUnavailableError` re-raised. |
| `test_summarise_image_uses_vision_when_supported` | Image bytes base64-encoded into `image_url` part. |
| `test_summarise_image_falls_back_to_filename_when_no_vision` | No-vision path uses filename-based prompt. |
| `test_get_text_context_uses_filename_when_text_below_threshold` | <40 chars → filename fallback. |
| `test_get_text_context_returns_extracted_text_when_long_enough` | Long text returned verbatim. |

### test_summary_workflow_service.py (pre-existing, 1 test)
`_generate_summary` calls ingest first, passes its extracted text to summary service.

### test_system_log_service.py
| Test | What it covers |
|---|---|
| `test_log_persists_event_with_normalized_level` | Level upper-cased; context JSON-serialised. |
| `test_log_captures_correlation_id_from_trace_context` | Uses `get_current_trace_id()` when no explicit id. |
| `test_log_explicit_correlation_id_wins` | Explicit `correlation_id` beats context. |
| `test_cleanup_old_logs_deletes_only_older_than_retention` | Rows older than retention cutoff deleted. |
| `test_cleanup_old_logs_with_nothing_stale_returns_zero` | No-op when nothing stale. |
| `test_get_recent_filters_by_level_and_component` | `level=` + `component=` filters. |

---

## tests/ (root)

### test_main_args.py (pre-existing, 2 tests)
CLI `parse_args` defaults + override flags.

### test_main_tracing.py (pre-existing, 3 tests)
`_request_trace_input` captures search-body query, ignores invalid JSON, skips non-search endpoints.

### test_versioning.py (pre-existing, 2 tests)
`VERSION` file matches `pyproject.toml`; env var override preferred.

---

## Mock infrastructure (where the fakes live)

| Mock target | Location |
|---|---|
| `httpx.MockTransport` for llama-server HTTP | `tests/services/test_llm_client.py:36-40` |
| `llm_client.achat` / `aembed` / `achat_with_vision` monkeypatch | `tests/services/test_summary_service_image.py`, `test_rename_service.py`, `test_schedule_extraction_service.py`, `test_classification_service.py` |
| `process_single_file` swap | `tests/api/test_organize_routes.py:18-30` |
| `_get_summary_workflow` dependency override | `tests/api/test_summary_routes.py:16-22` |
| `_get_classifier` dependency override | `tests/api/test_settings_categories_routes.py:18-26` |
| `FakeLightRAG` / `FakeRagService` / `FakeIngest` / `FakeDocStatus` | `tests/api/test_search.py:22-160`, `tests/services/test_background_ingest.py:17-112` |
| FastAPI lifespan stubs (`init_langfuse`, `llm_client.startup`, `RagService.setup`, `BackgroundIngestWorker.start`, `run_migrations`) | `tests/conftest.py:88-130` |
| In-memory SQLite (`sqlite+aiosqlite:///:memory:`) | `tests/conftest.py:24-43` |

---

## How to run

```bash
uv sync --extra dev

uv run pytest -q                  # all 260 tests
uv run pytest -m unit -q          # 120 fast tests, no FastAPI lifespan
uv run pytest -m integration -q   # 53 route tests
uv run pytest -m "not llm" -q     # explicit skip of llm-marked tests (none currently)

# Single file
uv run pytest tests/services/test_classification_service.py -v
```

---

## Known coverage gaps (deferred)

- **SSE `/api/summary/stream` mid-stream error event**: today the workflow runs *before* `event_generator()` starts, so the generator can only emit `chunk` + `done`. Pre-stream errors are covered by `test_summary_stream_returns_503_on_capability_error`. If the route is later refactored to stream from the workflow itself, add a `test_summary_stream_emits_error_event_when_workflow_fails_mid_stream` case.
- **Search service pure helpers** (`_chunk_score`, `_has_ordered_token_phrase`, `_normalize_filename_search_text`, `_search_tokens`): covered indirectly through `test_search.py` integration tests. Add direct unit tests if a regression is hit.
- **Alembic migration smoke test**: `run_migrations()` against an empty SQLite would catch broken revisions before they ship. Pending decision on whether migrations run in CI.
