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

## `app/api/settings/` — Settings & Categories

All configuration-related endpoints are grouped under `/api/settings`.
The folder structure:

```
app/api/settings/
├── __init__.py          ← Combines sub-routers under /api/settings
├── categories.py      ← CRUD for classification categories
└── base_path.py       ← Default base path setting
```

### Categories — `/api/settings/categories`

| Endpoint | Method | What it does |
|---|---|---|
| `/api/settings/categories` | GET | List categories (active only by default) |
| `/api/settings/categories` | POST | Create category + generate embedding |
| `/api/settings/categories/{id}` | GET | Get one category |
| `/api/settings/categories/{id}` | PATCH | Update category (re-embeds if name/description changes) |
| `/api/settings/categories/{id}` | DELETE | Delete category |

**Key behavior:** When you create/update a category, the embedding is auto-generated from `name + description` and stored as JSON in the `embedding` column. The single `description` field may contain both prose and comma-separated keyword phrases. Changing `name` or `description` triggers automatic re-embedding.

**Manual path flag:** Setting `destination_path` via PATCH marks `is_path_manual=true` — that category is excluded from auto-updates when the default base path changes.

### Default Base Path — `/api/settings/default-base-path`

| Endpoint | Method | What it does |
|---|---|---|
| `/api/settings/default-base-path` | GET | Get current default base path |
| `/api/settings/default-base-path` | PUT | Set base path + auto-update non-manual categories |

**Key behavior:** When the base path is set to e.g. `/Users/you/KlinFiles`, every category where `is_path_manual=false` gets its `destination_path` auto-updated to `/Users/you/KlinFiles/{category.name}`.

---

## `app/api/history.py` — Read-Only Audit Log

| Endpoint | Method | What it does |
|---|---|---|
| `GET /api/history` | GET | Recent entries (optional `?action=organized` filter) |
| `GET /api/history/file/{file_id}` | GET | History for a specific file |

---

**Next:** [The Full Organize Pipeline →](./06-organize-pipeline.md)
