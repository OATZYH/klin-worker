# เปรียบเทียบ Search Flow: วิธีปัจจุบัน vs ถ้าใช้ RAG-Anything เต็มรูปแบบ

> เอกสารนี้เขียนสำหรับคนที่ยังไม่เข้าใจระบบ ค่อยๆ อ่านจากบนลงล่างได้เลย

---

## เกริ่นนำ — เข้าใจสถานการณ์ปัจจุบันก่อน

โปรเจกต์ Klin **ใช้ library ชื่อ RAG-Anything อยู่แล้ว** (ดูได้จาก `app/services/ai/rag_service.py`) แต่ใช้แค่ "เปลือก" ของมัน — คือใช้ระบบ ingest/parse เอกสารและ vector DB แต่ **ปิดส่วนสำคัญที่สุดของ RAG-Anything ทิ้ง** นั่นคือ Knowledge Graph (KG) extraction

ดูได้ที่ `rag_service.py:134-173`:

```python
if lightrag is not None and not settings.rag_enable_kg_extraction:
    _disable_lightrag_kg_extraction(lightrag)       # ← ปิด KG ของ LightRAG
    _disable_raganything_kg_extraction(rag_engine)  # ← ปิด multimodal KG ของ RAGAnything
```

ดังนั้นคำถาม **"ถ้าเปลี่ยนมาใช้ RAGAnything สำหรับ search"** ในที่นี้ตีความว่า:

> **เปิดใช้ KG extraction กลับมา และใช้ `rag.aquery()` แทน `chunks_vdb.query()`**

---

## ภาพรวมความแตกต่าง

```
═══════════════════════════════════════════════════════════════════
  ปัจจุบัน                          │  ถ้าใช้ RAG-Anything เต็ม
═══════════════════════════════════════════════════════════════════
  chunks_vdb.query(query, top_k)    │  rag.aquery(query, mode="...")
                                    │
  → list of file paths              │  → ข้อความคำตอบ (generated)
  → ไม่ใช้ LLM                       │  → ใช้ LLM สังเคราะห์คำตอบ
  → ไม่ใช้ KG                        │  → ใช้ KG (entities + relations)
  → เร็ว ~ms                         │  → ช้า ~วินาที
  → เหมาะกับ "หาไฟล์"                │  → เหมาะกับ "ตอบคำถาม"
═══════════════════════════════════════════════════════════════════
```

---

## ส่วนที่ 1: Flow ปัจจุบันสรุปสั้น

```
GET /api/search?q=สัญญาเช่า
        │
        ▼
   search_files()
        │
        ├─► Filename match (SQLite)
        │   └─ ตรวจ "สัญญาเช่า" ⊂ ชื่อไฟล์
        │
        └─► chunks_vdb.query("สัญญาเช่า", top_k=60)
                │
                ▼
            [chunks เรียงตาม cosine similarity]
                │
                ▼
            กรอง (lexical match + score threshold)
                │
                ▼
            ดึง file_path → dedupe → map กลับเป็น File records
        │
        ▼
   merge + return list of files
```

**คุณสมบัติสำคัญ:**
- ไม่มี LLM call เลย
- ไม่มี Knowledge Graph
- ผลลัพธ์เป็น **list ของไฟล์**

---

## ส่วนที่ 2: ถ้าเปลี่ยนเป็น RAG-Anything เต็มรูปแบบ — Flow จะเปลี่ยนยังไง

### Flow ใหม่

```
GET /api/search?q=สัญญาเช่า
        │
        ▼
   search_files()
        │
        ├─► Filename match (เหมือนเดิม)
        │
        └─► rag.aquery("สัญญาเช่า", mode="hybrid")
                │
                ▼
            ┌─────────────────────────────────────┐
            │  RAG-Anything ทำงานหลายขั้นภายใน:    │
            │                                     │
            │  1. แปลง query เป็น embedding       │
            │  2. ค้นใน chunks_vdb (vector)        │
            │  3. ค้น entities ใน KG (local)      │
            │  4. ค้น relations ใน KG (global)    │
            │  5. รวม context ทั้งหมด              │
            │  6. ส่งให้ LLM สังเคราะห์คำตอบ        │
            └─────────────────────────────────────┘
                │
                ▼
            generated text answer
                │
                ▼
            (ต้อง parse คำตอบเพื่อแยก file references — ยุ่งยาก)
```

### สิ่งที่ต้องเปลี่ยนใน Code

| จุด | ปัจจุบัน | ใหม่ |
|---|---|---|
| Settings | `rag_enable_kg_extraction = False` | ต้อง **เปิดเป็น True** |
| Search call | `chunks_vdb.query(query, top_k)` | `rag_engine.aquery(query, mode="...")` |
| Output handling | iterate chunks → ดึง `file_path` | parse generated text → หา file references |
| Ingest cost | parse + chunk + embed | parse + chunk + embed + **KG extraction** (เพิ่ม LLM calls มหาศาล) |
| Storage | text_chunks + chunks_vdb + doc_status | + entities_vdb + relationships_vdb + graph_chunk_entity_relation |

### Mode ที่เลือกได้ใน `aquery`

RAG-Anything (สืบทอดจาก LightRAG) มี 4 mode หลัก:

| Mode | ใช้อะไร | ความหมายแบบเข้าใจง่าย |
|---|---|---|
| `naive` | chunks_vdb อย่างเดียว | คล้ายของเดิม แต่เพิ่มขั้น LLM generation |
| `local` | KG entities (local neighborhood) | "เกี่ยวกับสิ่งนี้โดยตรงคืออะไรบ้าง" |
| `global` | KG relations (global) | "ภาพรวมของหัวข้อนี้คืออะไร" |
| `hybrid` | local + global + vector | รวมทุกอย่าง (ช้าสุด แม่นสุด) |

---

## ส่วนที่ 3: Knowledge Graph คืออะไร และช่วย search ได้ยังไง

### KG คืออะไรในบริบทนี้

ตอน ingest เอกสาร LightRAG จะ "ส่งเนื้อหาให้ LLM อ่าน" แล้วให้สกัด:

1. **Entities** — สิ่งของ/บุคคล/แนวคิดที่ปรากฏในเอกสาร
   - เช่น `"บริษัท ABC"`, `"สัญญาเช่า 2024"`, `"นายสมชาย"`
2. **Relations** — ความสัมพันธ์ระหว่าง entities
   - เช่น `("นายสมชาย", "ลงนาม", "สัญญาเช่า 2024")`

แล้วเก็บลงใน graph database (พร้อม embedding ของแต่ละ entity/relation)

ตัวอย่าง:

```
       "นายสมชาย"
            │
            │ ลงนาม
            ▼
      "สัญญาเช่า 2024" ────เกี่ยวข้องกับ────► "บริษัท ABC"
            │
            │ มีระยะเวลา
            ▼
        "2 ปี"
```

### KG ช่วย Search ได้จริงไหม สำหรับ use case ของ Klin

> **คำตอบสั้น: ได้ประโยชน์น้อย ไม่คุ้มต้นทุน สำหรับ "หาไฟล์"**

ลองดูแต่ละ scenario:

#### Scenario A: User search "สัญญาเช่า"

| วิธี | ผลลัพธ์ |
|---|---|
| Vector-only (ปัจจุบัน) | คืนไฟล์ทุกอันที่มี chunk เกี่ยวข้องกับสัญญาเช่า ✓ |
| KG-enhanced | คืนคำตอบ "สัญญาเช่าในเอกสารของคุณมี 3 ฉบับ ได้แก่..." ต้อง parse เอง |

**สรุป:** Vector-only พอแล้ว KG ไม่ได้เพิ่มอะไร

#### Scenario B: User search "ใครเป็นคนเซ็นสัญญากับ ABC"

| วิธี | ผลลัพธ์ |
|---|---|
| Vector-only | คืน chunks ที่มีคำว่า "ABC" + "เซ็น" — ผู้ใช้ต้องอ่านเอง |
| KG-enhanced | สามารถ traverse graph: `ABC → สัญญา → ลงนามโดย → นายสมชาย` ตอบได้ตรงๆ |

**สรุป:** KG ช่วยจริง — **แต่นี่คือ Q&A ไม่ใช่ file search**

#### Scenario C: Klin search ใช้ทำอะไรจริง

ดูจาก `search-flow-explained.md` — Klin search ออกแบบให้ "**คืนรายชื่อไฟล์**" เพื่อให้ user คลิกไปดูไฟล์เอง ไม่ใช่ระบบถาม-ตอบ

ดังนั้น **ประโยชน์ของ KG ในบริบทนี้ต่ำมาก**

---

## ส่วนที่ 4: เปรียบเทียบรายข้อ

| ด้าน | ปัจจุบัน (`chunks_vdb.query`) | RAG-Anything เต็ม (`rag.aquery`) |
|---|---|---|
| **เป้าหมาย** | หา **ไฟล์** | ตอบ **คำถาม** |
| **Output** | List ของ file paths | Generated text |
| **ใช้ LLM ตอน query** | ❌ ไม่ใช้ | ✅ ใช้ทุกครั้ง |
| **ใช้ KG** | ❌ ไม่ใช้ | ✅ ใช้ (ถ้าเปิด KG extraction) |
| **ความเร็ว query** | ~50-200ms | ~2-10 วินาที (เพราะ LLM) |
| **ต้นทุนตอน ingest** | parse + embed | parse + embed + KG extraction (LLM heavy) |
| **Storage size** | ~1x | ~3-5x (graph + entity/relation vdb) |
| **Control filtering/ranking** | ✅ ทำเองได้ (lexical + threshold) | ⚠️ จำกัด — LightRAG จัดการให้ |
| **Privacy** | ไม่ออก network เลยตอน query | LLM ต้องเห็น query + retrieved chunks |
| **เหมาะกับ** | "เปิดไฟล์ที่เกี่ยวกับเรื่องนี้ให้หน่อย" | "สรุปให้หน่อยว่าเอกสารบอกอะไร" |

---

## ส่วนที่ 5: ข้อดี-ข้อเสียของการเปลี่ยน

### ข้อดี

1. **เข้าใจความสัมพันธ์ระหว่างเอกสารได้ลึกขึ้น** — เช่น "เอกสาร A อ้างถึง entity เดียวกับเอกสาร B"
2. **ตอบคำถามที่ต้อง reasoning ข้ามเอกสารได้** — เช่น "หาเอกสารทุกฉบับที่เกี่ยวกับลูกค้ารายนี้"
3. **มี multimodal context** (ถ้าเปิด VLM) — รูปและตารางในเอกสารถูกเข้าใจด้วย

### ข้อเสีย (สำคัญมาก)

1. **ต้นทุน LLM พุ่ง** ตอน ingest — KG extraction เรียก LLM ต่อ chunk หลายครั้ง สำหรับเอกสารใหญ่อาจกินเวลาเป็นชั่วโมงและสิ้นเปลือง compute มหาศาล
2. ** ช้าSearchลงหลายเท่า** — ต้องรอ LLM generate
3. **Output ไม่ตรงกับ use case** — ได้ "คำตอบ" แต่ต้องการ "list ไฟล์" — ต้อง parse คำตอบเองซึ่งไม่เสถียร
4. **เสีย control เรื่อง ranking** — โค้ดปัจจุบันมี logic lexical match + score threshold ที่ออกแบบมาเฉพาะ ถ้าใช้ aquery จะคุมยากกว่า
5. **Architecture เพิ่ม dependency บน LLM** — ถ้า llama-server ล่ม search ก็พังไปด้วย (ปัจจุบัน vector-only ยังทำงานได้ถ้า LLM ล่ม)

---

## ส่วนที่ 6: แนะนำสำหรับ Klin

> **อย่าเปลี่ยน** ถ้าเป้าหมายยังเป็น "file search" ตามที่ design ไว้

ระบบปัจจุบันคือ **architectural choice ที่ถูกต้อง** — ใช้แค่ vector layer ของ RAG-Anything โดยตั้งใจ ไม่ใช่ความบกพร่อง เพราะ:

- เป้าหมายคือคืน file paths ไม่ใช่ generated answers
- ไม่อยากให้ LLM เห็นเนื้อหาเอกสารตอน search (privacy)
- ต้องการความเร็วระดับ realtime
- ไม่อยากจ่ายต้นทุน KG extraction ตอน ingest

### ทางสายกลาง: เปิด KG บางส่วน

ถ้าอยากได้ประโยชน์จาก KG บ้างโดยไม่เสียคุณสมบัติเดิม สามารถ:

1. เปิด `rag_enable_kg_extraction = True` (ค่อยๆ ingest ใหม่)
2. **เก็บ search flow เดิมไว้** (`chunks_vdb.query`) สำหรับ file retrieval
3. เพิ่ม endpoint ใหม่ `/api/ask` ที่ใช้ `rag.aquery(mode="hybrid")` สำหรับ Q&A โดยเฉพาะ

แบบนี้จะได้ของสองอย่างแยกกัน: **search (เร็ว) + ask (ฉลาด)**

---

## สรุปท้ายเอกสาร

```
คำถาม: "เปลี่ยนมาใช้ RAG-Anything สำหรับ search ดีไหม?"

คำตอบ:
  - ใช้ RAG-Anything อยู่แล้ว แค่ปิด KG ไว้โดยตั้งใจ
  - ถ้าเปิด KG + เปลี่ยนเป็น aquery จะได้คำตอบจาก LLM
    แทน list ของไฟล์ → ไม่ตรงกับ use case
  - KG เด่นเรื่อง Q&A ไม่ใช่ file retrieval
  - ทางที่ดี: เก็บ search ปัจจุบันไว้, เพิ่ม Q&A endpoint แยก
```
