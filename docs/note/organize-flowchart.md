# Organize Flow — Mermaid Flowchart

```mermaid
flowchart TD
    A([POST /api/organize]) --> B[สำหรับแต่ละ filepath]

    B --> C{ไฟล์ถูก lock?}
    C -- YES --> C1([return locked_result])
    C -- NO --> D[Acquire per-file asyncio.Lock]

    D --> E[1. Scan metadata\nScannerService\nsize · SHA-256 · extension]
    E --> E1{scan error?}
    E1 -- YES --> E2([return error result])
    E1 -- NO --> F

    F[2. Upsert File record\nSQLite\nFile table] --> G{is new file?}
    G -- YES --> G1[is_new_file = True\nfile_changed = True]
    G -- NO --> G2{hash เปลี่ยน?}
    G2 -- YES --> G3[file_changed = True]
    G2 -- NO --> G4[file_changed = False]

    G1 & G3 & G4 --> H

    H[3. คำนวณ Analysis Fingerprint\nMD5 of active categories + parser profile]

    H --> I{Cache check}

    I -- "ไฟล์ไม่เปลี่ยน\n+ fingerprint ตรง\n+ ไม่ force" --> FULL_HIT
    I -- "ไฟล์ไม่เปลี่ยน\n+ มี cached analysis\n+ fingerprint ไม่ตรง\n+ ไม่ force" --> PARTIAL_HIT
    I -- "ไฟล์ใหม่/เปลี่ยน\nหรือ force=True" --> FULL_RUN

    subgraph FULL_HIT ["✅ Full Cache Hit  (<100ms)"]
        FH1[ดึง summary, names, scores จาก SQLite]
        FH2[Log history: organized_cached]
        FH3([return OrganizeFileResult])
        FH1 --> FH2 --> FH3
    end

    subgraph PARTIAL_HIT ["⚡ Partial Cache Hit (Reclassify only)"]
        PH1[ดึง summary + names เดิม]
        PH2[4. AI availability check\nRAG · LLM · Embedding]
        PH3{AI พร้อม?}
        PH4[5. Reclassify\nget_file_embedding + classify_with_embedding]
        PH5[Update categories_hash ใน SQLite]
        PH6[Log history: organized_reclassified]
        PH7([return OrganizeFileResult])
        PH1 --> PH2 --> PH3
        PH3 -- NO --> PH_ERR([return AI unavailable error])
        PH3 -- YES --> PH4 --> PH5 --> PH6 --> PH7
    end

    subgraph FULL_RUN ["🔄 Full Run"]
        FR1[4. AI availability check\nRAG · LLM general · Embedding]
        FR2{AI พร้อม?}
        FR3[5. Fast Docling Parse\nBackgroundIngestWorker.prepare\nenqueue_for_rag=False\nได้ content_list + extracted_text]
        FR4[6. Schedule Extraction\nScheduleExtractionService\nดึง calendar events จากเนื้อหา]
        FR5[7. Summary Generation\nSummaryService\nLLM สร้างสรุป 3-5 ประโยค]
        FR6[8. Rename Suggestion\nRenameService\nLLM แนะนำชื่อ snake_case สูงสุด 3 ชื่อ]
        FR7[9. Save FileAnalysis\nSQLite\nsummary + suggested_names + categories_hash]
        FR8[10. Classification\nClassificationService\nfile embedding vs category embeddings\ncosine similarity → score %]
        FR9[11. Enqueue Background RAG Ingest\nหลัง LLM งานทั้งหมดเสร็จ\nrich docling → LightRAG insert]
        FR10[12. Log history: organized]
        FR11([return OrganizeFileResult\nsuggested_names · categories · schedule])

        FR1 --> FR2
        FR2 -- NO --> FR_ERR([return AI unavailable error])
        FR2 -- YES --> FR3
        FR3 --> FR4 --> FR5 --> FR6 --> FR7 --> FR8 --> FR9 --> FR10 --> FR11
    end
```

---

## Core Flow (Happy Path)

```mermaid
flowchart LR
    A([filepath]) --> B[scan\nSHA-256]
    B --> C[upsert File\nSQLite]
    C --> D{cache?}

    D -- full hit --> Z([return cached])
    D -- partial hit --> E2[reclassify\nembedding] --> Z2([return])
    D -- full run --> E

    E[fast docling parse\ncontent_list + extracted_text]
    E --> F[schedule extract\nLLM]
    F --> G[summarise\nLLM]
    G --> H[suggest names\nLLM]
    H --> I[save FileAnalysis\nSQLite]
    I --> J[classify\nembedding cosine sim]
    J --> K[enqueue RAG\nbackground]
    K --> L([return result])
```

---

## หมายเหตุ

- **Lock** ป้องกัน concurrent request บนไฟล์เดียวกัน
- **Fingerprint** = MD5(active category ids+names+descriptions + parser profile) — ใช้ invalidate cache เมื่อ category ถูกแก้
- **RAG enqueue ถูก defer** จนกว่า LLM งานทั้งหมด (schedule/summary/rename/classify) จะเสร็จ เพื่อไม่แย่ง llama-server slot
- ระบบ **ไม่แตะไฟล์จริง** ทั้งหมดเป็น read-only ฝั่ง filesystem
