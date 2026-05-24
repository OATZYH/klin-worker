# Search Flow — Mermaid Flowchart

```mermaid
flowchart TD
    A([GET /api/search\nquery string]) --> B[normalize query\nlowercase + strip]

    B --> C{query ว่าง?}
    C -- YES --> C1[ตรวจ RAG status\n+ pending_count]
    C1 --> C2([return empty result\nพร้อม semantic_status])
    C -- NO --> D

    D[โหลดไฟล์ทั้งหมดจาก SQLite\n_load_known_files\nFile JOIN FileAnalysis] --> E & F

    E["🔤 Filename Match\n_filename_matches\nnormalize separators\nsubstring match ชื่อไฟล์"]

    F["🧠 Semantic Search\n_semantic_file_paths"]

    subgraph SEM ["Semantic Search Pipeline"]
        S1{RAG service ready?}
        S2{LightRAG init OK?}
        S3{chunks_vdb พร้อม?}
        S4["chunks_vdb.query\ntop_k = limit × 3\nVector similarity search"]
        S5["_semantic_chunk_diagnostics\nกรองแต่ละ chunk"]

        S1 -- NO --> S1E([status: not_ready])
        S1 -- YES --> S2
        S2 -- NO --> S2E([status: degraded])
        S2 -- YES --> S3
        S3 -- NO --> S3E([status: not_ready])
        S3 -- YES --> S4 --> S5
    end

    subgraph FILTER ["กรอง Chunks  (_semantic_chunk_diagnostics)"]
        F1["สำหรับแต่ละ chunk"] --> F2["โหลด stored chunk\n_load_stored_chunk\nจาก lightrag.text_chunks"]
        F2 --> F3["ตรวจ Lexical Match\n_has_ordered_token_phrase\nordered token phrase in content"]
        F3 --> F4{lexical match?}
        F4 -- YES --> F5["✅ Accept\nเสมอ ไม่ดู score"]
        F4 -- NO --> F6{score ≥ score_only_threshold?}
        F6 -- YES --> F7["✅ Accept\nscore-only pass"]
        F6 -- NO --> F8["❌ Reject\nfiltered out"]
        F5 & F7 --> F9["extract file_path\nfrom chunk / stored_chunk"]
        F9 --> F10["deduplicate paths\nordered by vector score"]
    end

    F --> SEM --> FILTER
    FILTER --> G

    subgraph STATUSCHECK ["ตรวจ doc_status + pending_count"]
        SC1["_semantic_status_from_doc_status\nตรวจ LightRAG doc_status._data"]
        SC2{มี failed docs?}
        SC3{มี pending docs?}
        SC4{ingest pending_count > 0?}
        SC1 --> SC2
        SC2 -- YES --> SC5["semantic_status = degraded"]
        SC2 -- NO --> SC3
        SC3 -- YES --> SC6["semantic_status = pending"]
        SC3 -- NO --> SC4
        SC4 -- YES --> SC7["semantic_status = pending"]
        SC4 -- NO --> SC8["semantic_status = ready"]
    end

    G["semantic paths\n(ordered by similarity)"] --> STATUSCHECK

    E --> H
    STATUSCHECK --> H

    H["🔀 Merge Results\n_append_unique\nfilename matches ก่อน\nตามด้วย semantic matches"]

    H --> I["map semantic paths → File records\nby_path / by_basename lookup"]
    I --> J["สร้าง FileSearchResultItem\nสำหรับแต่ละ row"]
    J --> K([return FileSearchResponse\nresults · semantic_status · semantic_error])
```

---

## Core Flow (Happy Path)

```mermaid
flowchart LR
    A([query]) --> B[normalize]
    B --> C[load all files\nSQLite]

    C --> D[filename match\nsubstring]
    C --> E[chunks_vdb.query\nvector similarity\ntop-k chunks]

    E --> F{lexical match?}
    F -- YES --> G[accept chunk]
    F -- NO --> H{score ≥ threshold?}
    H -- YES --> G
    H -- NO --> I[reject]

    G --> J[extract file_path\ndeduplicate]

    D --> K[merge\nfilename first\nthen semantic]
    J --> K
    K --> L([return FileSearchResponse])
```

---

## Chunk Acceptance Logic (ย่อ)

```mermaid
flowchart LR
    C[chunk จาก\nchunks_vdb] --> L{lexical\nmatch?}
    L -- YES --> ACC["✅ Accept"]
    L -- NO --> SC{score ≥\nscore_only\nthreshold?}
    SC -- YES --> ACC
    SC -- NO --> REJ["❌ Reject"]
```

---

## หมายเหตุ

| องค์ประกอบ | ค่า |
|---|---|
| `top_k` | `limit × 3` (เก็บ buffer สำหรับ filter) |
| Lexical match | Ordered phrase match + plural folding (`reports` → `report`) |
| Score threshold | `search_semantic_score_only_min_score` (สำหรับ chunks ที่ไม่มี lexical match) |
| Merge order | Filename matches ก่อน → semantic matches ต่อ (ไม่ซ้ำกัน) |
| semantic_status | `ready` / `pending` / `degraded` / `not_ready` |
