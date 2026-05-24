# Search Feature Workflow

## Overview

Search feature ใช้ **Hybrid Search** — ผสมระหว่าง filename matching (SQLite) และ semantic search (LightRAG vector DB) เพื่อค้นหาไฟล์ที่ user เคย organize แล้ว

**Endpoint:** `POST /api/search/files`

---

## Architecture Diagram

```
Client
  │
  ▼
POST /api/search/files
  │  body: { "query": "invoice 2024" }
  │
  ▼
app/api/search.py  ──── @observe(name="search.files")
  │
  │  injects: db (AsyncSession), rag (RagService), ingest (BackgroundIngestWorker)
  │
  ▼
app/services/search_service.py :: search_files()
  │
  ├─ [1] normalize query  →  lowercase + strip
  │       └─ empty query? → return early (status only, no results)
  │
  ▼
_search_known_files()
  │
  ├─ [2] load_known_files (SQLite)
  │       SELECT files LEFT JOIN file_analysis
  │
  ├─ [3] filename_match (in-memory)
  │       normalize → tokenize → substring match
  │       ranked_rows ← filename hits (priority)
  │
  ├─ [4] _semantic_file_paths() ──── @observe(name="search.semantic_lightrag")
  │       │
  │       ├─ check rag.is_ready
  │       ├─ ensure LightRAG initialized  (_ensure_lightrag_initialized)
  │       ├─ chunks_vdb.query(query, top_k=limit*3)
  │       └─ _semantic_chunk_diagnostics()
  │               ├─ per chunk: load stored chunk from text_chunks store
  │               ├─ lexical match  (_has_ordered_token_phrase)
  │               ├─ score filter   (>= search_semantic_min_score OR score_only_min_score)
  │               └─ collect unique file_paths  → SemanticSearchOutcome
  │
  ├─ [5] check doc_status  (_semantic_status_from_doc_status)
  │       ├─ failed docs  → status = "degraded"
  │       └─ pending docs → status = "pending"
  │
  ├─ [6] map semantic paths → known File rows (by_path / by_basename)
  │       _append_unique → ranked_rows (semantic hits appended after filename hits)
  │
  └─ [7] finalize response
          FileSearchResponse {
            results: [FileSearchResultItem, ...],
            semantic_status: "ready" | "pending" | "degraded" | "not_ready",
            semantic_error: str | None,
            indexing_pending_count: int,
          }
```

---

## Step-by-Step Workflow

### Step 1 — Query Normalization

**File:** `app/services/search_service.py:693` (`search_files`)

```
raw_query → lowercase → strip → query
```

ถ้า query ว่างเปล่าหลัง normalize → return `FileSearchResponse` ว่างทันที พร้อม semantic_status ตาม state ปัจจุบันของ RAG

---

### Step 2 — Load Known Files (SQLite)

**File:** `app/services/search_service.py:173` (`_load_known_files`)

```sql
SELECT files.*, file_analysis.*
FROM files
LEFT JOIN file_analysis ON file_analysis.file_id = files.id
```

โหลดทุกไฟล์ที่ระบบรู้จักมาไว้ใน memory (`rows: list[tuple[File, FileAnalysis | None]]`)

**Models ที่ใช้:**
- `app/db/models.py:108` — `File` (original_path, current_path, hash, size, extension)
- `app/db/models.py:137` — `FileAnalysis` (summary, suggested_names, processed_at)

---

### Step 3 — Filename Match (In-Memory)

**File:** `app/services/search_service.py:183` (`_filename_matches`)

ทำ substring match บน filename ด้วย logic ที่ normalize ก่อน:

```
"Invoice 2024" → "invoice 2024"
                  ↓
              normalize separators (_, -, . → space)
              → "invoice 2024"

ไฟล์ "invoice_jan_2024.pdf" → "invoice jan 2024"
  → match ✓
```

**Helper functions:**
- `_normalize_filename_search_text()` — แปลง separator เป็น space, lowercase
- `_search_tokens()` — tokenize + plural folding (e.g., "invoices" → "invoice")
- `_has_ordered_token_phrase()` — ตรวจ ordered token sequence

ผลที่ได้เป็น `matched_rows` → เพิ่มลง `ranked_rows` ก่อน (priority สูงกว่า semantic)

---

### Step 4 — Semantic Search (LightRAG Vector DB)

**File:** `app/services/search_service.py:409` (`_semantic_file_paths`)

#### 4.1 Readiness Check

ตรวจว่า `RagService.is_ready` และ LightRAG initialized สำเร็จก่อน ถ้าไม่ → return `SemanticSearchOutcome(status="not_ready")`

#### 4.2 Vector Query

```python
chunks = await chunks_vdb.query(query, top_k=limit * 3)
```

- `chunks_vdb` คือ LightRAG's chunk vector store (nano-vectordb หรือ backend ที่ config ไว้)
- query ต่อตรงกับ vector store ไม่ผ่าน `rag.aquery()` (ซึ่งจะ generate text answer) → ได้ raw chunks + scores

#### 4.3 Chunk Filtering (`_semantic_chunk_diagnostics`)

**File:** `app/services/search_service.py:304`

สำหรับแต่ละ chunk:

```
chunk
  │
  ├─ load stored chunk จาก lightrag.text_chunks (ดึง file_path + content)
  │
  ├─ lexical match?  (_has_ordered_token_phrase(query, chunk_text))
  │     → ถ้า lexical match → accept เสมอ (ไม่ต้อง pass score threshold)
  │
  └─ score filter:
        score >= search_semantic_min_score (0.55)  → accept (score_match)
        score >= search_semantic_score_only_min_score (0.85) → accept (score_only)
        ต่ำกว่าทั้งคู่ → filtered out
```

**Score thresholds** (จาก `app/core/config.py`):
| Setting | Default | ความหมาย |
|---|---|---|
| `search_semantic_min_score` | `0.55` | Semantic score ขั้นต่ำเมื่อมี lexical match ด้วย |
| `search_semantic_score_only_min_score` | `0.85` | Score ขั้นต่ำเมื่อไม่มี lexical match |

ผลลัพธ์: `SemanticPayloadDiagnostics` → unique `file_paths` ที่ผ่าน filter

**Helper functions ที่ใช้ใน chunk filtering:**
- `_chunk_score()` — อ่าน distance/score จาก chunk dict
- `_chunk_text_for_lexical_match()` — รวม content + file_path + full_doc_id เป็น text
- `_chunk_file_path()` — resolve file_path จาก chunk หรือ stored_chunk
- `_load_stored_chunk()` — async load chunk จาก `lightrag.text_chunks`

---

### Step 5 — Doc Status Check

**File:** `app/services/search_service.py:202` (`_semantic_status_from_doc_status`)

ตรวจ LightRAG's `doc_status._data` (persisted status ของทุก document ที่ ingest):

| Doc Status | Semantic Status ที่ return |
|---|---|
| มี failed docs | `"degraded"` |
| มี pending/processing docs | `"pending"` |
| ปกติ | ไม่ override (ใช้ status จาก step 4) |

นอกจากนี้ถ้า `BackgroundIngestWorker.pending_count() > 0` → status = `"pending"` (ยังมีไฟล์รอ ingest)

---

### Step 6 — Map Semantic Paths → Known Files

**File:** `app/services/search_service.py:626`

Semantic search คืน `file_path` strings — ต้อง map กลับไปหา `File` record ใน SQLite:

```python
# lookup tables สร้างจาก rows ทั้งหมด
by_path:     { current_path → row, original_path → row }
by_basename: { filename.ext → row }  # fallback ถ้า path ไม่ตรง

for semantic_path in semantic.paths:
    row = by_path.get(semantic_path) or by_basename.get(basename)
    _append_unique(ranked_rows, row, seen_ids)  # dedupe by file id
```

ผลลัพธ์: `ranked_rows` มีทั้ง filename hits (ด้านหน้า) + semantic hits (ด้านหลัง) โดยไม่ซ้ำกัน

---

### Step 7 — Finalize Response

**File:** `app/services/search_service.py:657` → `_to_search_item()`

แปลง `(File, FileAnalysis | None)` → `FileSearchResultItem`:

```python
FileSearchResultItem(
    id=file_record.id,
    file_name=Path(current_path).name,
    file_type=extension.lstrip(".").lower(),
    size_bytes=file_record.size,
    folder=str(Path(current_path).parent),
    last_edited=Path(current_path).stat().st_mtime,  # จาก filesystem จริง
    path=file_record.current_path,
)
```

**`last_edited` resolution order:**
1. filesystem `stat().st_mtime` (จริง)
2. `analysis.processed_at` (fallback ถ้าไฟล์ถูกลบ)
3. `file_record.created_at` (last resort)

---

## File References

| File | Role |
|---|---|
| `app/api/search.py` | FastAPI router — รับ request, inject dependencies |
| `app/services/search_service.py` | Search pipeline ทั้งหมด (main logic) |
| `app/services/ai/rag_service.py` | RagService wrapper รอบ RAGAnything/LightRAG |
| `app/services/organize/background_ingest.py` | BackgroundIngestWorker — queue ingest + pending count |
| `app/models/request.py:221` | `FileSearchRequest` — request body schema |
| `app/models/response.py:280` | `FileSearchResultItem`, `FileSearchResponse` — response schema |
| `app/db/models.py:108` | `File` ORM model |
| `app/db/models.py:137` | `FileAnalysis` ORM model |
| `app/core/config.py:161` | `search_semantic_min_score`, `search_semantic_score_only_min_score` |
| `app/observability/tracing.py` | `@observe`, `start_as_current_observation`, `update_current_span` |

---

## Key Data Structures

### `SemanticSearchOutcome`
```python
@dataclass(frozen=True, slots=True)
class SemanticSearchOutcome:
    paths: list[str]           # unique file paths ที่ผ่าน filter
    status: SemanticStatus     # "ready" | "pending" | "degraded" | "not_ready"
    error: str | None
    error_category: str | None
```

### `SemanticPayloadDiagnostics`
```python
@dataclass(frozen=True, slots=True)
class SemanticPayloadDiagnostics:
    paths: list[str]
    chunk_count: int
    accepted_chunk_count: int
    filtered_chunk_count: int
    lexical_match_count: int
    score_match_count: int
    score_only_match_count: int
    semantic_min_score: float | None
    semantic_score_only_min_score: float | None
    # ...
```

### `FileSearchResponse`
```python
class FileSearchResponse(BaseModel):
    results: list[FileSearchResultItem]
    semantic_status: Literal["ready", "pending", "degraded", "not_ready"]
    semantic_error: str | None
    indexing_pending_count: int
```

---

## Result Ranking Order

```
ranked_rows = [
  # 1. Filename hits (เรียงตาม SQLite row order)
  # 2. Semantic hits ที่ไม่ซ้ำกับ filename hits (เรียงตาม vector score)
]
```

ไม่มี global re-ranking ระหว่าง filename กับ semantic — filename hits จะอยู่หน้าเสมอ

---

## Semantic Status Values

| Status | เมื่อไหร่ | ความหมาย |
|---|---|---|
| `ready` | RAG init OK, ไม่มี pending | Semantic search ทำงานปกติ |
| `pending` | มี files ใน ingest queue หรือ LightRAG pending | ผล search อาจไม่ complete |
| `degraded` | LightRAG init fail หรือมี failed docs | Semantic search ล้มเหลว, ใช้ filename only |
| `not_ready` | RagService ยัง init ไม่เสร็จ | Semantic search ไม่พร้อม |

---

## Observability (Langfuse Spans)

Search request หนึ่งจะสร้าง spans ดังนี้:

```
search.files                         ← router (observe decorator)
└─ search.normalize_query            ← span
└─ search.load_known_files           ← span (SQLite query)
└─ search.filename_match             ← span
└─ search.semantic_lightrag          ← span (observe decorator)
   └─ search.semantic_chunk_filter   ← span (chunk scoring loop)
└─ search.map_semantic_paths         ← span
└─ search.finalize_results           ← span
```

ทุก span ถูก instrument ด้วย `start_as_current_observation()` จาก `app/observability/tracing.py` ซึ่ง wrap Langfuse SDK และทำ safe no-op เมื่อ Langfuse disabled

---

## Dependency Injection (FastAPI)

```python
# app/api/search.py
@router.post("/files")
async def search_files(
    body: FileSearchRequest,
    db: AsyncSession = Depends(get_db),          # SQLite session
    rag: RagService = Depends(_get_rag),          # singleton จาก app.main
    ingest: BackgroundIngestWorker = Depends(...), # singleton จาก app.main
)
```

ทั้ง `RagService` และ `BackgroundIngestWorker` เป็น application-level singletons ที่ init ตอน startup lifespan ของ FastAPI

---

## Background Ingest Connection

Search feature ไม่ ingest เอง แต่ใช้ `BackgroundIngestWorker` เพื่อ:
1. ตรวจ `pending_count()` → บอก frontend ว่ายังมีไฟล์รอ index
2. ส่งผล `semantic_status = "pending"` เพื่อ hint ให้ user รอ

Ingest flow แยกออกไปที่ organize pipeline (`app/services/organize/background_ingest.py`) ซึ่งทำ:
- `DoclingParser` (fast profile) → extract text inline
- Queue `IngestJob` → background worker loop → `RAGAnything.insert_content_list()` → LightRAG indexes chunks
