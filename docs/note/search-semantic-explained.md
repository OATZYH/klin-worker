# หลักการ Semantic Search ด้วย `chunks_vdb.query` และการเปรียบเทียบกับ `lightrag.query(mode="hybrid")`

## ภาพรวม

ระบบ search ของ Klin ไม่ได้ใช้ LightRAG ในแบบที่ส่วนใหญ่จะนึกถึง (คือไม่ได้เรียก `lightrag.query()`) แต่เจาะลงไปที่ internal layer ของ LightRAG โดยตรง นั่นคือ **Vector Database ของ Chunks** ซึ่งเก็บ embedding ของเนื้อหาแต่ละชิ้นที่ถูก index ไว้

---

## องค์ประกอบที่เกี่ยวข้อง

```
search_files()
    └── _search_known_files()
            ├── _load_known_files()          # โหลดไฟล์ทั้งหมดจาก SQLite
            ├── _filename_matches()          # Lexical match ชื่อไฟล์
            ├── _semantic_file_paths()       # <-- หัวใจของ semantic search
            │       ├── chunks_vdb.query()  # Vector similarity search
            │       └── _semantic_chunk_diagnostics()
            │               ├── _load_stored_chunk()         # โหลด chunk จาก text_chunks storage
            │               ├── _has_ordered_token_phrase()  # Lexical fallback บน chunk content
            │               └── _chunk_score()               # อ่าน similarity score
            └── (merge results: filename matches + semantic matches)
```

### ชั้น Infrastructure ของ LightRAG

| Layer | ชื่อ attribute | หน้าที่ |
|---|---|---|
| `RagService._rag` | `rag_engine` | RAG-Anything engine |
| `rag_engine.lightrag` | `lightrag` | LightRAG instance |
| `lightrag.chunks_vdb` | Vector DB ของ chunks | เก็บ embedding ของแต่ละ text chunk |
| `lightrag.text_chunks` | Storage ของ chunk content | เก็บ text จริงและ metadata ของ chunk |
| `lightrag.doc_status` | Status ของเอกสาร | ติดตามสถานะการ index แต่ละไฟล์ |

---

## หลักการทำงานของ `chunks_vdb.query`

### 1. Vector Embedding คืออะไร

เมื่อไฟล์ถูก ingest เข้า LightRAG มันจะถูกแบ่งเป็น **chunks** (ท่อนๆ ของข้อความ) แล้วแต่ละ chunk จะถูกแปลงเป็น **embedding vector** — ตัวเลขมิติสูง (เช่น 1536 ตัวเลข) ที่แทนความหมายของข้อความนั้น

ข้อความที่มีความหมายใกล้กันจะมี vector ที่ "ใกล้กัน" ในเชิงเรขาคณิต

### 2. `chunks_vdb.query(query, top_k=N)` ทำอะไร

```python
chunks = await chunks_vdb.query(query, top_k=query_params["top_k"])
```

1. **แปลง query เป็น embedding vector** โดยใช้ embedding model เดียวกันกับที่ใช้ตอน index
2. **คำนวณ similarity** ระหว่าง query vector กับทุก chunk vector ใน vector DB (ปกติใช้ cosine similarity หรือ dot product)
3. **คืน top-K chunks** ที่ similarity สูงสุด พร้อม `score` หรือ `distance`

ผลที่ได้เป็น list ของ chunk dict ที่มีโครงสร้างประมาณนี้:
```json
{
  "id": "chunk_abc123",
  "content": "...",
  "file_path": "/path/to/file.pdf",
  "score": 0.87
}
```

### 3. การกรอง Chunks (`_semantic_chunk_diagnostics`)

เนื่องจาก `chunks_vdb.query` คืน chunks ทุกอย่างตาม vector similarity ล้วนๆ ระบบจึงมี **สองชั้นการกรอง** เพื่อให้ผลที่ได้มีความเกี่ยวข้องจริง:

#### ชั้นที่ 1: Lexical Match (ความสำคัญสูงกว่า)

```python
lexical_text = _chunk_text_for_lexical_match(chunk, stored_chunk)
lexical_match = _has_ordered_token_phrase(query, lexical_text)
```

ตรวจว่า query tokens ปรากฏในเนื้อหา chunk ตามลำดับ (ordered phrase match) เช่น query `"annual report"` จะต้องเจอ `"annual"` ตามด้วย `"report"` ในข้อความ ไม่ใช่แค่แต่ละคำโดยอิสระ

- ใช้ plural folding เบื้องต้น: `"reports"` → `"report"`
- text ที่นำมาตรวจรวมทั้ง content ใน chunk response และ content จาก `text_chunks` storage (โหลดผ่าน `_load_stored_chunk`)
- หาก lexical match → **รับ chunk ไว้เสมอ** โดยไม่ดู score

#### ชั้นที่ 2: Score Threshold (สำหรับ chunks ที่ lexical miss)

```python
score_match = chunk_score >= settings.search_semantic_min_score

elif chunk_score >= settings.search_semantic_score_only_min_score:
    score_only_match_count += 1
else:
    filtered_chunk_count += 1
    continue  # ตัดทิ้ง
```

มี threshold สองระดับ:
- `search_semantic_min_score` — threshold ปกติ (ใช้ใน diagnostics/logging)
- `search_semantic_score_only_min_score` — threshold จริงสำหรับ chunks ที่ไม่มี lexical match (ต้องสูงกว่า)

ตรรกะ: ถ้า chunk ไม่มี lexical match เลย ต้อง "มั่นใจ" ด้าน semantic similarity มากกว่า จึงใช้ threshold ที่สูงขึ้น

### 4. สร้าง Output เป็น File Paths

หลังกรอง chunks แล้ว ระบบดึง `file_path` จากแต่ละ chunk ที่ผ่าน โดย **deduplicate** และ **รักษาลำดับ** ตามที่ vector DB จัด (score สูงมาก่อน) เพราะหลายๆ chunk อาจมาจากไฟล์เดียวกัน

```python
if file_path not in seen:
    ordered_paths.append(file_path)
    seen.add(file_path)
```

จากนั้น paths เหล่านี้จะถูก map กลับไปยัง File records ใน SQLite เพื่อสร้าง `FileSearchResultItem`

---

## การเปรียบเทียบกับ `lightrag.query(mode="hybrid")`

| ด้าน | `chunks_vdb.query` (วิธีที่ใช้) | `lightrag.query(mode="hybrid")` |
|---|---|---|
| **เป้าหมาย** | ค้นหา **ไฟล์** ที่เกี่ยวข้อง | สร้าง **คำตอบ** จากเนื้อหา |
| **Output** | List ของ file paths | ข้อความคำตอบ (generated text) |
| **LLM involvement** | ไม่ใช้ LLM ในขั้นค้นหา | ใช้ LLM สร้างคำตอบจาก retrieved context |
| **Knowledge Graph** | ไม่ใช้ | ใช้ (hybrid = vector + graph traversal) |
| **ความเร็ว** | เร็วกว่า (pure vector search) | ช้ากว่า (graph + LLM generation) |
| **ความยืดหยุ่น** | ควบคุม filter/scoring ได้เอง | LightRAG จัดการให้ทั้งหมด |
| **Use case** | "แสดงไฟล์ที่เกี่ยวข้องกับ query นี้" | "ตอบคำถามจากเอกสาร" |

### ทำไมถึงเลือก `chunks_vdb.query` แทน `lightrag.query`

1. **ต้องการ file paths ไม่ใช่คำตอบ** — ระบบ search ต้องการรายชื่อไฟล์ที่เกี่ยวข้อง ไม่ใช่ generated answer
2. **ควบคุม ranking/filtering ได้** — ใส่ lexical scoring และ threshold เองได้
3. **ไม่เสียค่า LLM call** — `lightrag.query` จะ call LLM เพื่อ synthesize คำตอบซึ่งไม่จำเป็น
4. **Hybrid mode ของ LightRAG ใช้ knowledge graph** — graph traversal มี overhead และออกแบบมาสำหรับ Q&A ไม่ใช่ file retrieval

### Hybrid ใน `lightrag.query(mode="hybrid")` หมายถึงอะไร

LightRAG มี 4 modes: `naive`, `local`, `global`, `hybrid`
- **naive** — vector search บน chunks อย่างเดียว (คล้ายสิ่งที่ระบบทำแต่ไม่มี LLM generation)
- **local** — ค้นหา entities ใน knowledge graph แบบ local context
- **global** — ค้นหา relationships ระดับ global ใน graph
- **hybrid** — รวม local + global graph + vector chunks แล้วให้ LLM สังเคราะห์คำตอบ

ดังนั้น `lightrag.query(mode="hybrid")` จะทำงานหนักกว่า `chunks_vdb.query` มาก และ output ที่ได้เป็นคำตอบสำหรับมนุษย์อ่าน ไม่ใช่ structured file paths

---

## Flow สรุป

```
User query: "สัญญาเช่า"
     │
     ▼
normalize: "สัญญาเช่า" (lowercase, strip)
     │
     ├─► Filename match (SQLite)
     │       "สัญญาเช่า" ⊂ filename → matched rows
     │
     └─► chunks_vdb.query("สัญญาเช่า", top_k=60)
             │
             ▼
         [chunk₁ score=0.92, chunk₂ score=0.85, chunk₃ score=0.43, ...]
             │
             ▼
         _semantic_chunk_diagnostics()
             ├── chunk₁: lexical match ✓ → accept
             ├── chunk₂: score ≥ score_only_threshold ✓ → accept
             └── chunk₃: no lexical, score < threshold ✗ → reject
             │
             ▼
         unique file paths (ordered by score)
             │
             ▼
         map paths → File records (SQLite)
             │
             ▼
merge(filename_matches + semantic_matches) → FileSearchResponse
```

---

## สิ่งที่ควรรู้เพิ่มเติม

- **`doc_status`** ใช้ตรวจสอบว่าไฟล์บางไฟล์ยัง index ไม่เสร็จหรือ failed ก่อนที่จะ return semantic status เป็น `ready`, `pending`, หรือ `degraded`
- **`ingest.pending_count()`** นับไฟล์ที่รอ index อยู่ใน queue — ถ้ามี pending ให้ semantic_status เป็น `"pending"` แทน `"ready"` เพื่อบอก client ว่าผล search ยังไม่ครบถ้วน
- การใช้ `suppress_trace_text_payloads()` ครอบ `chunks_vdb.query` เพื่อไม่ให้ content ของ chunks ถูก log ออกไป (privacy)
