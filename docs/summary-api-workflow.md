# Summary API — Workflow Breakdown

Two endpoints share the same single-file summary pipeline. The JSON route returns the final summary in one response, and the SSE route transports that same result with `meta`, `chunk`, and `done` events.

---

## Endpoints

| Endpoint | Handler | Response |
|---|---|---|
| `POST /api/summary` | `summarise_file` | JSON `SummaryResponse` |
| `POST /api/summary/stream` | `summarise_file_stream` | SSE `text/event-stream` |

---

## Shared Flow (both endpoints)

```
POST /api/summary[/stream]
  │
  ├─ Depends: _get_summary_workflow()
  │     ├─ SummaryService()                     # AI summarizer (text/image)
  │     └─ BackgroundIngestWorker (from app.main)
  │
  └─ workflow.summarise_file(file_path, db)
        │   [SummaryWorkflowService]
        │
        ├─ llm_client.ensure_general_available() # raises AiCapabilityUnavailableError if LLM down
        │
        └─ _generate_summary(db, file_path)
              │
              ├─ ingest_worker.prepare(file_path, enqueue_for_rag=False)
              │   → extract text via docling/markitdown
              │
              ├─ SummaryService.summarise(file_path, extracted_text)
              │   ├─ image?  → llm_client.achat_with_vision (vision model, base64)
              │   └─ text?   → llm_client.achat (LLM, text prompt)
              │
              └─ _persist_summary(db, file_path, summary)
                    → INSERT/UPDATE file_analysis.summary

  returns: str | None
```

---

## Response Behaviour

### `POST /api/summary` — Blocking JSON

```
summary_text
  │
  ├─ empty? → SummaryResponse(hardcoded plain-text fallback)
  │
  returns: SummaryResponse { summary, processing_time_ms }
```

### `POST /api/summary/stream` — SSE Streaming

```
emit: event: chunk  { delta: summary_text or fallback text }
emit: event: done   { processing_time_ms }
```

---

## Helper Functions

| Function | Type | Purpose |
|---|---|---|
| `_empty_summary_text()` | sync | Fallback text when no summary can be generated |

---

## Service References

| Symbol | File | Role |
|---|---|---|
| `SummaryWorkflowService` | `services/summary_workflow_service.py` | Generate and persist a fresh summary per request |
| `SummaryService` | `services/ai/summary_service.py` | Per-file LLM/vision summarisation |
| `BackgroundIngestWorker` | `services/background_ingest.py` | Extract text from file (docling/markitdown) |
| `File`, `FileAnalysis` | `db/models.py` | DB models for cache read/write |
| `observe`, `update_current_span` | `observability/tracing.py` | Langfuse trace decoration |

---

## Error Paths

| Where | Error | Behaviour |
|---|---|---|
| `ensure_general_available()` | `AiCapabilityUnavailableError` | 503 HTTP via `to_service_unavailable_http_exception` |
| `summarise_file` (other) | `Exception` | `summary = None` → empty-state response |
