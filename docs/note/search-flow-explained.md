# Search Flow — อธิบาย Flow ทั้งหมดของระบบ Search

## ภาพรวม

`GET /api/search` รับ query string จาก client แล้วค้นหาไฟล์ที่เกี่ยวข้องจากสองแหล่งพร้อมกัน:
- **Filename match** — ตรวจชื่อไฟล์ตรงๆ จาก SQLite
- **Semantic search** — ค้นหาจากเนื้อหาเอกสารผ่าน LightRAG vector DB

ระบบ **ไม่ใช้ LLM เลยในขั้นค้นหา** และ **ไม่แตะไฟล์จริง** — ทุกอย่างอ่านจาก SQLite และ vector DB เท่านั้น

---

## องค์ประกอบหลัก

| Component | หน้าที่ |
|---|---|
| `File` (SQLite) | เก็บ metadata ของไฟล์ที่เคย organize แล้ว |
| `FileAnalysis` (SQLite) | เก็บ summary ของแต่ละไฟล์ |
| `lightrag.chunks_vdb` | Vector DB ที่เก็บ embedding ของแต่ละ chunk |
| `lightrag.text_chunks` | Storage ของ chunk content จริง (text + metadata) |
| `lightrag.doc_status` | บันทึกสถานะการ index ของแต่ละไฟล์ |
| `BackgroundIngestWorker` | ใช้ตรวจ `pending_count()` — ไฟล์รอ index อยู่กี่ไฟล์ |

---

## Flow หลัก

```
GET /api/search?q=...
     │
     ▼
search_files()
     │
     ├─ 1. Normalize query
     │
     ├─ query ว่าง? → return empty result พร้อม semantic_status
     │
     └─ _search_known_files()
             │
             ├─ 2. โหลดไฟล์ทั้งหมดจาก SQLite
             │
             ├─ 3. Filename match
             │
             ├─ 4. Semantic search (_semantic_file_paths)
             │       ├─ ตรวจ RAG readiness
             │       ├─ chunks_vdb.query (vector similarity)
             │       └─ กรอง chunks (_semantic_chunk_diagnostics)
             │               ├─ Lexical match → accept เสมอ
             │               └─ Score threshold → accept ถ้าสูงพอ
             │
             ├─ 5. ตรวจ semantic_status (doc_status + pending_count)
             │
             ├─ 6. Map semantic paths → File records
             │
             └─ 7. Merge + return FileSearchResponse
```

---

## อธิบายแต่ละขั้นตอน

### ขั้นที่ 1: Normalize Query

```python
query = _normalize_query(raw_query)  # strip + lowercase
```

แปลง query ให้เป็น lowercase และตัด whitespace หัวท้ายออก ถ้า query ว่างหลัง normalize ระบบ return ทันทีพร้อม `semantic_status` ปัจจุบัน โดยไม่ค้นหาอะไร

---

### ขั้นที่ 2: โหลดไฟล์ทั้งหมดจาก SQLite

```python
rows = await _load_known_files(db)
# SELECT File, FileAnalysis (LEFT JOIN) WHERE file_id = File.id
```

โหลด **ทุกไฟล์** ที่เคยผ่าน organize มาแล้ว พร้อม `FileAnalysis` ที่ join มาด้วย (LEFT JOIN ดังนั้นไฟล์ที่ยังไม่มี analysis ก็ติดมาด้วย)

ผลที่ได้เป็น list ของ `(File, FileAnalysis | None)` ซึ่งจะใช้ใน filename match และ map semantic paths ภายหลัง

---

### ขั้นที่ 3: Filename Match

```python
matched_rows = _filename_matches(rows, query)
```

**วิธีทำงาน:**

1. Normalize ทั้ง query และชื่อไฟล์ให้อยู่ในรูปแบบเดียวกันก่อน — แปลง separator ทุกชนิด (`-`, `_`, `.`, space) ให้เป็น space แล้ว lowercase:

```python
def _normalize_filename_search_text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.lower())).strip()
```

ตัวอย่าง: `"Annual_Report-2024.pdf"` → `"annual report 2024 pdf"`

2. ตรวจว่า normalized query เป็น **substring** ของ normalized filename หรือเปล่า

```
query: "annual report"       → normalized: "annual report"
file:  "Annual_Report-Q3.pdf" → normalized: "annual report q3 pdf"
"annual report" ⊂ "annual report q3 pdf" → match ✓
```

ผลที่ได้คือ list ของ rows ที่ชื่อไฟล์ตรง จะถูก push เข้า `ranked_rows` เป็นชุดแรก (ความสำคัญสูงกว่า semantic)

---

### ขั้นที่ 4: Semantic Search (`_semantic_file_paths`)

ขั้นนี้แบ่งเป็นสองส่วนย่อย: **ตรวจ readiness** และ **query + filter chunks**

#### ส่วนที่ 4a: ตรวจ RAG Readiness

ก่อน query vector DB ระบบตรวจ 3 ชั้น:

```
RagService.is_ready?
  NO  → return status: not_ready
  YES ↓

rag_engine._ensure_lightrag_initialized()?
  FAIL → return status: degraded
  OK   ↓

lightrag.chunks_vdb มี .query() ไหม?
  NO  → return status: not_ready
  YES ↓

→ ดำเนินการ query ต่อ
```

ถ้า readiness check ล้มเหลวที่ชั้นไหน ระบบยังคง return ผล filename match ให้ได้ — semantic search เป็นแค่ส่วนเสริม ไม่ทำให้ทั้ง request ล้มเหลว

#### ส่วนที่ 4b: Vector Query

```python
chunks = await chunks_vdb.query(query, top_k=limit * 3)
```

`top_k = limit × 3` เพื่อเผื่อ buffer สำหรับการกรองในขั้นถัดไป เช่น ถ้า limit = 20 จะ query 60 chunks ก่อน

`chunks_vdb.query` ทำงานโดย:
1. แปลง query string เป็น embedding vector ด้วย embedding model เดียวกับที่ใช้ตอน index
2. คำนวณ cosine similarity กับทุก chunk vector ใน DB
3. คืน top-K chunks ที่ใกล้เคียงที่สุด พร้อม score

ผลที่ได้:
```json
[
  {"id": "chunk_abc", "content": "...", "file_path": "/path/to/file.pdf", "score": 0.92},
  {"id": "chunk_def", "content": "...", "file_path": "/path/to/other.docx", "score": 0.85},
  ...
]
```

#### ส่วนที่ 4c: กรอง Chunks (`_semantic_chunk_diagnostics`)

`chunks_vdb.query` คืน chunks ตาม vector similarity ล้วนๆ ซึ่งอาจมี noise ระบบจึงกรองด้วยสองชั้น:

**สำหรับแต่ละ chunk:**

```python
stored_chunk = await _load_stored_chunk(lightrag, chunk_id)
```

โหลด chunk ฉบับเต็มจาก `lightrag.text_chunks` โดยใช้ `chunk_id` — เพราะ chunk ที่ได้จาก vector query อาจมี content ไม่ครบ ต้องดึงเพิ่มจาก text storage

จากนั้นตรวจสองอย่าง:

**ชั้นที่ 1: Lexical Match (สำคัญกว่า)**

```python
lexical_text = _chunk_text_for_lexical_match(chunk, stored_chunk)
# รวม content + file_path + full_doc_id จากทั้ง chunk และ stored_chunk

lexical_match = _has_ordered_token_phrase(query, lexical_text)
```

ตรวจว่า query tokens ปรากฏใน chunk content **ตามลำดับ** (ordered phrase):
- `"annual report"` → ต้องเจอ token `"annual"` ตามด้วย `"report"` ติดกัน
- ใช้ plural folding: `"reports"` → `"report"` เพื่อให้ match ได้

ถ้า lexical match → **รับ chunk เสมอ ไม่ดู score**

**ชั้นที่ 2: Score Threshold (สำหรับ chunk ที่ lexical miss)**

```python
if chunk_score >= settings.search_semantic_score_only_min_score:
    # accept
else:
    continue  # reject
```

ถ้า chunk ไม่มี lexical match เลย ต้อง "มั่นใจ" ด้าน semantic มากกว่า จึงใช้ threshold สูงกว่า `search_semantic_min_score` ปกติ

| สถานการณ์ | ผลลัพธ์ |
|---|---|
| lexical match | ✅ รับเสมอ |
| ไม่มี lexical, score ≥ threshold สูง | ✅ รับ |
| ไม่มี lexical, score ต่ำกว่า threshold | ❌ ตัดทิ้ง |

**สร้าง file paths:**

หลังกรองแล้ว ดึง `file_path` จากแต่ละ chunk ที่ผ่าน โดย:
- ดูจาก chunk ก่อน ถ้าไม่มีดูจาก stored_chunk
- **Deduplicate** — ไฟล์เดียวกันอาจมีหลาย chunk ผ่าน นับแค่ครั้งเดียว
- **รักษาลำดับ** ตาม vector score (chunk score สูงมาก่อน)

---

### ขั้นที่ 5: ตรวจ `semantic_status`

ระบบ report สถานะของ semantic search กลับไปด้วยทุกครั้ง เพื่อให้ client รู้ว่าผลที่ได้ "ครบ" แค่ไหน

ตรวจสองอย่างแยกกัน:

**5a: `doc_status` ใน LightRAG**

```python
doc_status_outcome = _semantic_status_from_doc_status(rag)
```

สแกน `lightrag.doc_status._data` ทุก record:
- มีไฟล์ที่ status = `failed` → `semantic_status = "degraded"`
- มีไฟล์ที่ status = `pending` หรือ `processing` → `semantic_status = "pending"`

**5b: `pending_count()` จาก ingest worker**

```python
pending_count = ingest.pending_count()
if pending_count > 0 and semantic_status == "ready":
    semantic_status = "pending"
```

ถ้ามีไฟล์รอ index อยู่ใน queue แสดงว่าผล semantic search ยังไม่ครบ

| `semantic_status` | ความหมาย |
|---|---|
| `ready` | index ครบ ผลเชื่อถือได้ |
| `pending` | มีไฟล์รอ index อยู่ ผลอาจไม่ครบ |
| `degraded` | มีไฟล์ที่ index ล้มเหลว |
| `not_ready` | LightRAG ยังไม่พร้อม semantic search ทำงานไม่ได้ |

---

### ขั้นที่ 6: Map Semantic Paths → File Records

```python
for semantic_path in semantic.paths:
    row = by_path.get(semantic_path) or by_basename.get(Path(semantic_path).name)
    _append_unique(ranked_rows, row, seen_ids)
```

LightRAG คืน path ของไฟล์ตาม path ที่ตอน index — แต่ path อาจเปลี่ยนไปแล้ว (เช่น ไฟล์ถูก rename ผ่าน organize) ระบบจึง lookup สองแบบ:

1. **by_path** — ตรง path เป๊ะ (ทั้ง `current_path` และ `original_path`)
2. **by_basename** — ตามชื่อไฟล์อย่างเดียว fallback กรณี path เปลี่ยน

ถ้าหา record ไม่เจอ (ไฟล์ถูกลบออกจาก SQLite ไปแล้ว) — skip ไป ไม่ error

---

### ขั้นที่ 7: Merge + Return

```python
response = FileSearchResponse(
    results=[_to_search_item(file_record, analysis) for file_record, analysis in ranked_rows],
    semantic_status=semantic_status,
    semantic_error=semantic_error,
    indexing_pending_count=pending_count,
)
```

`ranked_rows` ถูก build ตามลำดับนี้:
1. **Filename matches** ก่อน — ชื่อตรงกว่า แสดงก่อน
2. **Semantic matches** ต่อ — เฉพาะที่ยังไม่มีใน ranked_rows (deduplicate ด้วย `seen_ids`)

แต่ละ row แปลงเป็น `FileSearchResultItem` ที่มี:
- `file_name`, `file_type`, `size_bytes`, `folder`, `path`
- `last_edited` — อ่านจาก filesystem mtime จริง (fallback ไป `processed_at` ถ้าไฟล์หาย)

---

## ข้อควรรู้เพิ่มเติม

- **ไม่มี LLM** ในขั้นค้นหาเลย ทั้งหมดใช้ vector similarity + lexical match
- **Filename matches มี priority สูงกว่า semantic** — ถ้าไฟล์ match ทั้งสองทาง จะปรากฏแค่ครั้งเดียวในตำแหน่งของ filename match
- **Semantic search fail gracefully** — ถ้า LightRAG ไม่พร้อม ยังคืน filename matches ให้ได้ปกติ
- **`suppress_trace_text_payloads()`** ครอบ `chunks_vdb.query` เพื่อไม่ให้ content ของ chunks ถูก log ออกไป (privacy)
- ไฟล์ที่ semantic path ชี้ไปแต่ไม่มีใน SQLite (เคย index แต่ถูกลบ record) จะถูก skip เงียบๆ ไม่ทำให้ request ล้มเหลว
