# 🌐 API Layer (The Routes)

## `app/api/organize.py` — `POST /api/organize`

**The main endpoint.** Accepts a list of file paths, runs the full AI pipeline, returns results.

The route handler orchestrates all services via dependency injection:
```python
@router.post("/api/organize")
async def organize_files(
    body: OrganizeRequest,
    db: AsyncSession = Depends(get_db),
    scanner: ScannerService = Depends(_get_scanner),
    rag: RagService = Depends(_get_rag),
    classifier: ClassificationService = Depends(_get_classifier),
    summary_svc: SummaryService = Depends(_get_summary),
    rename_svc: RenameService = Depends(_get_rename),
    history_svc: HistoryService = Depends(_get_history),
):
    for filepath in body.filepaths:
        result = await _process_single_file(filepath, ...)
    return OrganizeResponse(results=results)
```

Each file goes through `_process_single_file()` — see [Organize Pipeline](./06-organize-pipeline.md).

---

## `app/api/categories.py` — CRUD for Categories

| Endpoint | Method | What it does |
|---|---|---|
| `GET /api/categories` | GET | List categories (active only by default) |
| `POST /api/categories` | POST | Create category + generate embedding |
| `GET /api/categories/{id}` | GET | Get one category |
| `PATCH /api/categories/{id}` | PATCH | Update category (re-embeds if name/description changes) |
| `DELETE /api/categories/{id}` | DELETE | Delete category |

**Key behavior:** When you create/update a category, the embedding is auto-generated from `"{name}. {description}. {keywords_text}"` and stored as JSON in the `embedding` column. Changing `name`, `description`, or `keywords_text` triggers automatic re-embedding.

---

## `app/api/history.py` — Read-Only Audit Log

| Endpoint | Method | What it does |
|---|---|---|
| `GET /api/history` | GET | Recent entries (optional `?action=organized` filter) |
| `GET /api/history/file/{file_id}` | GET | History for a specific file |

---

**Next:** [The Full Organize Pipeline →](./06-organize-pipeline.md)
