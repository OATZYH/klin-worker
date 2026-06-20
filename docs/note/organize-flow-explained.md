# Organize Flow — อธิบาย Flow ทั้งหมดของระบบ Organize

## ภาพรวม

`POST /api/organize` คือ endpoint หลักที่รับรายชื่อไฟล์จาก client แล้ว "วิเคราะห์" ไฟล์เหล่านั้น — ระบบ**ไม่ได้ย้าย ไม่ได้เปลี่ยนชื่อ ไม่ได้ลบไฟล์จริงๆ** แต่ return ข้อมูลสำหรับให้ผู้ใช้ตัดสินใจเอง

สิ่งที่ระบบทำคือ:
- บอกว่าไฟล์นี้น่าจะเป็นหมวดหมู่อะไร (score %)
- แนะนำชื่อไฟล์ที่ดีกว่าเดิม
- ดึง event ปฏิทิน (ถ้ามีในเอกสาร)

---

## องค์ประกอบหลัก

| Service | หน้าที่ |
|---|---|
| `ScannerService` | อ่าน metadata ไฟล์จากระบบไฟล์ (ขนาด, hash, นามสกุล) |
| `BackgroundIngestWorker` | Parse เอกสารด้วย Docling แล้วส่งเข้า LightRAG (RAG indexing) |
| `ScheduleExtractionService` | ดึง calendar events จากเนื้อหาเอกสาร |
| `SummaryService` | สร้างสรุปเนื้อหาด้วย LLM |
| `RenameService` | แนะนำชื่อไฟล์ใหม่จากสรุป |
| `ClassificationService` | Score ไฟล์กับแต่ละ category ด้วย embedding |
| `HistoryService` | บันทึก history การ organize |
| `LockSettingsService` | ตรวจว่าไฟล์นี้ถูก lock ไว้ (ห้ามแตะ) |

---

## Flow หลัก (ต่อ 1 ไฟล์)

```
POST /api/organize
     │
     ▼
[สำหรับแต่ละ filepath]
     │
     ├─ ตรวจ lock settings → ถ้า locked: return locked result ทันที
     │
     └─ process_single_file()
             │
             ▼
         [acquire per-file lock]  ← ป้องกัน race condition ถ้า request เดียวกันมาซ้ำ
             │
             ▼
         _process_single_file_inner()
             │
             ├─ 1. Scan metadata
             ├─ 2. Upsert File record (SQLite)
             ├─ 3. Cache check
             │       ├─ Full hit  → return cached result ทันที
             │       └─ Partial hit → reclassify only
             ├─ 4. AI availability check
             ├─ 5. Parse (fast docling) — SYNC, ยังไม่ส่งเข้า RAG
             ├─ 6. Schedule extraction
             ├─ 7. Summary generation
             ├─ 8. Rename suggestion
             ├─ 9. Save analysis to SQLite
             ├─ 10. Classification (embedding)
             ├─ 11. Enqueue RAG ingest (background) ← ทำหลัง LLM งานเสร็จ
             └─ 12. Log history → return result
```

---

## อธิบายแต่ละขั้นตอน

### ขั้นที่ 0: Lock Check

```python
reason = lock_svc.get_lock_reason(filepath, lock_settings)
if reason:
    results[filepath] = build_locked_result(filepath, reason)
    continue
```

บางไฟล์ผู้ใช้อาจกำหนดว่า "ห้ามแตะ" — เช่นไฟล์ระบบหรือไฟล์ที่อยู่ใน directory เฉพาะ ถ้า locked จะ return error พร้อม reason ทันที ไม่ต้องทำขั้นตอนอื่น

---

### ขั้นที่ 1: Acquire Per-File Lock

```python
async with _file_locks[filepath]:
    ...
```

ใช้ `asyncio.Lock` แยกต่อ filepath เพื่อป้องกันกรณีที่ request สองอันมาพร้อมกันสำหรับไฟล์เดียวกัน (เช่น user กด organize ซ้ำ) ทำให้ไม่มีการ process ไฟล์เดิมสองครั้งพร้อมกัน

---

### ขั้นที่ 2: Scan Metadata (`ScannerService`)

```python
scan = await scanner.scan(filepath)
```

อ่านข้อมูลจาก filesystem โดยไม่แก้ไขอะไร:
- ตรวจว่าไฟล์มีอยู่จริง มีสิทธิ์อ่านได้ ไม่ใช่ directory ต้องห้าม
- คำนวณ **SHA-256 hash** ของเนื้อหาไฟล์ (ใช้เปรียบเทียบว่าไฟล์เปลี่ยนหรือเปล่า)
- ดึง size, extension, path

---

### ขั้นที่ 3: Upsert File Record (SQLite)

ค้นหา `File` record ใน SQLite ตาม path:
- **ไม่เคยเห็นไฟล์นี้** → สร้าง record ใหม่ (`is_new_file = True`)
- **เคยเห็นแล้ว** → update hash/size/extension ล่าสุด

จากนั้นเช็คว่าไฟล์ "เปลี่ยน" ไหม โดยเปรียบ hash ปัจจุบันกับที่บันทึกไว้ครั้งก่อน:
```python
file_changed = is_new_file or previous_hash != scan.sha256
```

---

### ขั้นที่ 4: Cache Check

นี่คือจุดที่ระบบ "ฉลาด" — ก่อนทำอะไรกับ AI ระบบจะตรวจก่อนว่าจำเป็นต้องทำใหม่หรือเปล่า โดยใช้สองสิ่งประกอบกัน: **file hash** และ **analysis fingerprint**

---

#### file hash คืออะไร และมาจากไหน

ตอน scan (ขั้นที่ 2) ระบบคำนวณ SHA-256 ของเนื้อหาไฟล์จริง แล้วเก็บไว้ใน `File.hash` ใน SQLite

ครั้งถัดไปที่ organize ไฟล์เดิม ระบบ hash ไฟล์ใหม่แล้วเทียบ:

```python
file_changed = is_new_file or previous_hash != scan.sha256
```

ถ้า hash ตรง = ไฟล์ไม่มีอะไรเปลี่ยนเลยนับจากครั้งก่อน

---

#### Analysis Fingerprint คืออะไร และมาจากไหน

Fingerprint เป็น MD5 hash ที่คำนวณ **ณ เวลา request นั้น** จากสองอย่าง:

```python
# organize_pipeline.py : get_analysis_fingerprint()

cat_str = "||".join(
    f"{c.id}:{c.name}:{(c.description or '').strip()}"
    for c in active_categories  # ดึงจาก SQLite Category table
)
return hashlib.md5(f"{cat_str}::{parser_fingerprint}".encode()).hexdigest()
```

1. **active categories** — เอา `id + name + description` ของทุก category ที่ `is_active=True` เรียงตาม id มาต่อกัน
2. **parser profile fingerprint** — config ของ fast docling parser (chunk size, overlap ฯลฯ)

แปลว่า ถ้าใครไปแก้ชื่อ category, แก้ description, เพิ่ม/ลบ category → fingerprint จะได้ค่าใหม่ทันที

fingerprint นี้ถูกเก็บไว้ใน `FileAnalysis.categories_hash` หลังจาก analyze ครั้งล่าสุด

---

#### การตรวจ cache เกิดขึ้นอย่างไร

```python
# ขั้นที่ 1: คำนวณ fingerprint ปัจจุบัน (จาก categories ที่อยู่ใน DB ตอนนี้)
current_analysis_fingerprint = await get_analysis_fingerprint(db)

# ขั้นที่ 2: เปรียบกับที่บันทึกไว้ครั้งก่อน
analysis_fingerprint_match = (
    has_cached_analysis
    and file_record.analysis.categories_hash == current_analysis_fingerprint
)
```

จากนั้นตัดสินใจด้วย condition สามอย่างรวมกัน:

```
file_changed?   fingerprint_match?   force?   → ผลลัพธ์
─────────────   ──────────────────   ──────   ─────────────────────────
False           True                 False    → Full Cache Hit
False           False                False    → Partial Hit (reclassify)
True (หรือ)     (ไม่สน)              True     → Full Run
```

---

#### กรณี Full Cache Hit

```
ไฟล์ไม่เปลี่ยน AND fingerprint ตรง AND ไม่ force
→ return cached result ทันที (<100ms)
```

ดึง summary, suggested_names, scores จากฐานข้อมูลแล้ว return เลย — ไม่มีการเรียก LLM เลยแม้แต่ครั้งเดียว

#### กรณี Partial Cache Hit (Reclassify only)

```
ไฟล์ไม่เปลี่ยน AND มี cached analysis AND fingerprint ไม่ตรง AND ไม่ force
→ เอา summary/name เดิม + รัน classification ใหม่
```

เกิดเมื่อผู้ใช้แก้ category (เพิ่ม/ลบ/แก้ description) — ไฟล์ยังเหมือนเดิม แต่ต้อง score กับ category ชุดใหม่ ไม่ต้อง summarise ซ้ำ

#### กรณี Full Run

```
ไฟล์ใหม่ OR ไฟล์เปลี่ยน OR force=True
→ ทำทุกขั้นตอน
```

---

#### ตัวอย่างที่เกิดขึ้นในชีวิตจริง

| สถานการณ์ | file hash | fingerprint | ผลลัพธ์ |
|---|---|---|---|
| Organize ซ้ำโดยไม่แก้อะไร | ตรง | ตรง | Full hit — return ทันที |
| แก้เนื้อหาไฟล์แล้ว organize ใหม่ | ไม่ตรง | — | Full run |
| แก้ description ของ category | ตรง | ไม่ตรง | Partial hit — reclassify |
| เพิ่ม category ใหม่ | ตรง | ไม่ตรง | Partial hit — reclassify |
| กด force=True | — | — | Full run เสมอ |

---

### ขั้นที่ 5: AI Availability Check

ก่อนจะทำ AI งานใดๆ ระบบจะตรวจสอบว่า service ทุกตัวพร้อมทำงาน:

```python
ai_errors = await _collect_organize_ai_errors(rag)
```

ตรวจ 3 อย่าง:
1. **RAG service** — LightRAG ready ไหม
2. **LLM general** — llama-server พร้อมรับ text generation ไหม
3. **LLM embedding** — embedding API พร้อมไหม

ถ้ามี error → return `OrganizeFileResult` พร้อม error message ทันที ไม่ดำเนินการต่อ

---

### ขั้นที่ 6: Fast Docling Parse + เตรียม RAG Job (`BackgroundIngestWorker`)

```python
prepared_ingest = await ingest.prepare(
    filepath=scan.original_path,
    trace_id=...,
    enqueue_for_rag=False,  # ← สำคัญ: ยังไม่ส่งเข้า RAG ตอนนี้
)
```

**ทำ synchronously (รอผล):**
- Parse ไฟล์ด้วย fast docling profile — ได้ `content_list` และ `extracted_text`
- สร้าง deferred RAG job ไว้รอ แต่**ยังไม่ enqueue**

**ทำไมยังไม่ enqueue?**
Background RAG ingest ต้องใช้ llama-server เหมือนกัน ถ้า enqueue ตอนนี้ background worker จะแย่ง LLM slot กับขั้นตอน summary/rename/classify ที่ต้องรอ user อยู่ จึง defer ไว้ทำหลังสุด

---

#### `content_list` คืออะไร

`content_list` คือ output ของ Docling parser — เป็น list ของ dict แต่ละอันแทน "หน่วย" ของเนื้อหาในเอกสาร จำแนกตาม type:

```python
[
    {"type": "text",     "text": "รายงานประจำปี 2567...", "page_idx": 0},
    {"type": "table",    "table_body": [...],              "page_idx": 1},
    {"type": "image",    "img_path": "/tmp/fig1.png",      "page_idx": 2},
    {"type": "equation", "latex": "E = mc^2",              "page_idx": 3},
]
```

โครงสร้างนี้ถูกใช้ใน **สองจุด** ที่แตกต่างกันโดยสิ้นเชิง:

| จุดใช้งาน | ใช้อะไรจาก content_list | เพื่ออะไร |
|---|---|---|
| **Schedule extraction** (ขั้นที่ 7) | แต่ละ item พร้อม `page_idx` | scan หน้าที่มีสัญญาณ schedule (meeting, flight, วันที่) แล้วส่งเฉพาะหน้านั้นให้ LLM |
| **Background RAG ingest** (ขั้นที่ 12) | ทุก item ที่ normalize ได้ | ส่งเข้า LightRAG เป็น structured content แทนการส่งไฟล์ดิบ ทำให้ RAG รู้ว่าอะไรคือ text/table/image แยกกัน |

#### `extracted_text` ต่างจาก `content_list` อย่างไร

```python
extracted_text = self._fast_parser.extract_text(content_list) or None
```

`extracted_text` คือการ flatten `content_list` ทั้งหมดออกมาเป็น plain string เดียว — เอาแค่ text content โดยไม่สนโครงสร้าง ใช้เป็น context ใน **summary generation** (ขั้นที่ 8) ซึ่งต้องการ string ธรรมดาส่งให้ LLM

```
content_list  →  structured (type, page, ข้อมูลดิบแต่ละก้อน)  →  schedule + RAG
extracted_text →  plain string                                  →  summary + rename
```

---

### ขั้นที่ 7: Schedule Extraction (`ScheduleExtractionService`)

```python
schedule_result = await schedule_svc.extract(
    file_path=scan.original_path,
    content_list=prepared_ingest.content_list,
    extracted_text=prepared_ingest.extracted_text,
)
```

ถามว่าเอกสารนี้มี calendar event ไหม:
- scan หน้าที่มีหลักฐานของ schedule (คำว่า meeting, flight, นัดหมาย, วันที่)
- ส่ง context เหล่านั้นให้ LLM พร้อม JSON schema
- LLM return events แต่ละอันพร้อม title, time, location, attendees
- กรอง: เฉพาะ event ที่มีวันที่ครบ และ**ไม่ผ่านแล้ว** (ต้องเป็นวันในอนาคต)
- รองรับวันไทย เช่น "พฤษภาคม", "ม.ค."

Result นี้จะถูกส่งกลับไปใน response และไม่ถูก save ลง SQLite (เป็น transient data)

---

### ขั้นที่ 8: Summary Generation (`SummaryService`)

```python
summary_text = await summary_svc.summarise(
    scan.original_path,
    extracted_text=extracted_text,
)
```

สร้างสรุปเนื้อหา 3-5 ประโยค:
- **Text files**: ส่ง extracted_text (จาก docling) เป็น context ให้ LLM สรุป
- **Image files**: encode base64 แล้ว call vision model
- **Fallback**: ถ้า vision ไม่พร้อม ใช้ชื่อไฟล์เพียงอย่างเดียว

Summary นี้สำคัญมาก เพราะถูกใช้ต่อในขั้น rename และ classification

---

### ขั้นที่ 9: Rename Suggestion (`RenameService`)

```python
suggested_names = await rename_svc.suggest_names(
    original_name=scan.file_name,
    extension=scan.extension,
    summary=summary_text,
)
```

LLM แนะนำชื่อไฟล์ใหม่ที่ดีกว่า:
- ใช้ summary + ชื่อไฟล์เดิม + นามสกุลเป็น input
- ตัด token ที่ซ้ำกับชื่อเดิมออก ไม่ให้ suggest ชื่อที่คล้ายเดิมเกินไป
- Return สูงสุด 3 ตัวเลือก ในรูปแบบ `snake_case.ext`
- ใช้ temperature 0.2 เพื่อความสม่ำเสมอ

---

### ขั้นที่ 10: Save Analysis (SQLite)

บันทึกลงสองตารางพร้อมกัน:

**ตาราง `file_analysis`** (1 row ต่อ 1 ไฟล์ — upsert)

| column | เก็บอะไร | ตัวอย่าง |
|---|---|---|
| `summary` | สรุปเนื้อหาจาก LLM | `"รายงานผลประกอบการไตรมาส 3..."` |
| `suggested_names` | JSON list ของชื่อที่แนะนำ | `'["q3_report", "quarterly_results"]'` |
| `categories_hash` | fingerprint ณ เวลา analyze | `"a3f2c1d..."` (MD5) |
| `processed_at` | timestamp ที่ save | auto-set |

`categories_hash` คือ fingerprint ที่ใช้ตรวจ cache ครั้งต่อไป — ถ้า category เปลี่ยนแล้ว hash ไม่ตรง ระบบจะ reclassify ใหม่

**ตาราง `category_scores`** (N rows ต่อ 1 ไฟล์ — เขียนทับทุกครั้ง)

| column | เก็บอะไร |
|---|---|
| `file_id` | FK → `files.id` |
| `category_id` | FK → `categories.id` |
	| `score` | cosine similarity (0.0–1.0) |

แต่ละ row คือคะแนนของไฟล์นี้กับ 1 category — มีกี่ category active ก็มีกี่ row

---

### ขั้นที่ 11: Classification (`ClassificationService`)

```python
file_embedding = await classifier.get_file_embedding(
    scan.original_path, summary=summary_text
)
scores = await classifier.classify_with_embedding(
    file_id=file_record.id, file_embedding=file_embedding, db=db
)
```

**วิธีทำงาน:**

1. สร้าง **file embedding** จาก `filename + summary` รวมกัน (เป็น vector ตัวเลข)
2. สร้าง **category embedding** จาก description ของแต่ละ category
3. คำนวณ **cosine similarity** ระหว่าง file vector กับแต่ละ category vector
4. แปลงเป็นเปอร์เซ็นต์ 0–100 และ save score ลง SQLite

ยิ่ง summary บอกเนื้อหาละเอียด ยิ่ง score แม่นยำ

---

### ขั้นที่ 12: Enqueue Background RAG Ingest

```python
if prepared_ingest is not None and prepared_ingest.deferred_job is not None:
    rag_status_after = await ingest.enqueue_after_organize(prepared_ingest)
```

หลัง LLM งานทั้งหมดเสร็จแล้ว — summary, rename, classify — ตอนนี้ถึงจะ enqueue RAG job

Background worker จะทำ:
- Rich docling parse (ละเอียดกว่า fast parse)
- Normalize content (จัดการรูปภาพ, ตาราง, สมการ)
- ลบ document เดิมออกจาก LightRAG ก่อน (ถ้ามี)
- Insert ใหม่เข้า LightRAG vector DB

งานนี้ทำใน background ไม่ block response

---

### ขั้นที่ 13: Log History + Return

```python
await history_svc.log(db=db, file_id=file_record.id, action="organized", metadata=...)
return OrganizeFileResult(
    file_id=file_record.id,
    suggested_names=suggested_names,
    categories=category_responses,
    schedule=schedule_result,
)
```

บันทึก history action พร้อม metadata (timing, RAG status, scores) แล้ว return ผลให้ client

---

## Summary Decision Tree

```
request มาถึง
     │
     ▼
ไฟล์ถูก lock?
  YES → return locked_result
  NO  ↓
     ▼
ไฟล์ไม่เปลี่ยน + fingerprint ตรง + ไม่ force?
  YES → return cached (full hit) ← เร็วมาก <100ms, ไม่เรียก LLM เลย
  NO  ↓
     ▼
ไฟล์ไม่เปลี่ยน + มี cached analysis + ไม่ force?
  YES → reclassify only (partial hit) ← ใช้แค่ embedding, ไม่ทำ summary/rename ใหม่
  NO  ↓
     ▼
Full run:
  scan → parse → schedule → summary → rename → save → classify → enqueue RAG → log
```

---

## ข้อควรรู้เพิ่มเติม

- **ไม่มีการแตะไฟล์จริง** ทั้งหมดเป็น read-only ฝั่ง file system
- **apply endpoint แยกกัน** — `POST /api/organize/apply` ถึงจะ "ยืนยัน" การ rename/move โดยบันทึกใน SQLite ว่า path เปลี่ยน (แต่ก็ยังไม่ได้ย้ายไฟล์จริงๆ ในระบบ — เป็น record update)
- **LLM slot contention** คือเหตุผลที่ RAG ingest ถูก defer จนกว่า LLM งานทั้งหมดจะเสร็จ — llama-server รองรับ 1 request ต่อครั้ง
- **Telemetry** บันทึก timing ทุก step ใน ms ผ่าน `OrganizeTelemetry` และส่งขึ้น Langfuse/tracing
