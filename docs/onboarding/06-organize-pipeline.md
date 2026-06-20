# Organize Pipeline Deep Dive

This document describes the live implementation in `app/api/organize.py`.

## Endpoint

`POST /api/organize`

Input:

```json
{
  "file_paths": ["/absolute/path/to/file.pdf"],
  "force": false
}
```

## Per-file Execution Flow

Each file is processed under a per-path async lock to prevent concurrent races.

### Step 1: Scan

`ScannerService.scan` validates and extracts:

1. normalized path
2. filename and extension
3. size
4. SHA-256 hash

If scan fails, return per-file error and skip remaining steps.

### Step 2: Upsert file row

Lookup by `files.current_path`.

- existing row -> update hash/size/extension
- missing row -> create new file row

Compute flags:

- `is_new_file`
- `file_changed`

### Step 3: Cache branch decision

Compute active categories hash from active categories (id/name/description).

#### Full cache hit

Condition:

- unchanged file
- categories hash match
- `force=false`

Behavior:

- return cached analysis and scores
- write `organized_cached` history

#### Partial cache reclassify

Condition:

- unchanged file
- analysis exists
- categories hash mismatch
- `force=false`

Behavior:

- reuse cached summary and suggested names
- recompute scores only
- update categories hash
- write `organized_reclassified` history

#### Full pipeline

Condition:

- new file, changed file, or `force=true`

Continue with steps below.

### Step 4: AI capability checks

Before expensive work:

1. `rag.ensure_ready()`
2. `llm_client.ensure_general_available()`
3. `llm_client.ensure_embedding_available()`

If unavailable, return structured per-file AI error.

### Step 5: Enqueue ingest (non-blocking for RAG insert)

`BackgroundIngestWorker.enqueue` does two things:

1. Inline parse with docling
2. Queue parsed content for background RAG insertion

Status is tracked in pipeline metadata (`queued`, `queue_full`, `parse_failed`, `skipped_image`, etc.).

### Step 6: Generate summary

`SummaryService.summarise`:

- image files -> vision flow
- text/docs -> use `TextCache` when available
- fallback when context is weak

### Step 7: Generate rename suggestions

`RenameService.suggest_names` returns a list of candidate names.

### Step 8: Save analysis

Upsert `file_analysis` with:

1. summary
2. suggested names (JSON list string)
3. categories hash

### Step 9: Classify

`ClassificationService` builds file embedding (filename plus summary when available), compares with category embeddings, persists top-k scores.

Response scores are converted to percentage format.

### Step 10: Write history

Write action:

- `organized`

History metadata includes:

1. suggested names
2. all raw scores
3. compact pipeline status data (`cache_reason`, `ai_status`, `rag_status`)
4. total elapsed time only

## Response Example

```json
{
  "results": {
    "/absolute/path/to/file.pdf": {
      "file_id": "uuid",
      "suggested_names": [
        "quarterly_finance_report.pdf",
        "q4_financial_summary.pdf"
      ],
      "categories": [
        {
          "category_id": "uuid",
          "name": "Finance & Invoices",
          "score": 91.2
        }
      ],
      "error": null
    }
  }
}
```

## Apply Decision Follow-up

After organize returns suggestions, UI calls `POST /api/organize/apply` to record user-confirmed rename/move decisions.

This updates DB state (`files.current_path`) and history actions (`renamed`, `moved`, `renamed_moved`).

## Debugging Checklist

When a result looks wrong:

1. Verify scanner output and hash behavior.
2. Confirm which cache branch executed.
3. Check AI capability status.
4. Inspect ingest enqueue status.
5. Inspect summary text quality.
6. Inspect category embeddings and scores.
7. Inspect Langfuse trace or system log timing breakdown.

You can now return to [01-overview.md](./01-overview.md) and run the smoke flow end-to-end.
