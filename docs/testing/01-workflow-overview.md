# Klin Worker — Workflow Overview

## What is this?

`klin-worker` is a **FastAPI backend** that acts as an AI-powered file organiser.  
It does **not** move or rename any file — it only **analyses** files and tells you:

- A plain-text **summary** of the file's content
- A **suggested rename** for the file
- Which **category** the file best fits into (with confidence scores)

---

## High-Level Architecture

```
Tauri / Client
      │
      │  POST /api/organize   {"filepaths": ["/abs/path/file.pdf"]}
      ▼
┌─────────────────────────────────────────────────────────┐
│                    FastAPI  (port 8000)                  │
│                                                         │
│  ┌──────────────┐                                       │
│  │ ScannerSvc   │  1. Read file metadata                │
│  │              │     (path, size, SHA-256, extension)  │
│  └──────┬───────┘                                       │
│         │                                               │
│  ┌──────▼───────┐                                       │
│  │ SQLite (DB)  │  2. Upsert File record                │
│  └──────┬───────┘                                       │
│         │                                               │
│  ┌──────▼───────┐                                       │
│  │  RAG-Anything│  3. Ingest file (embed → vector store)│
│  └──────┬───────┘                                       │
│         │                                               │
│  ┌──────▼───────┐                                       │
│  │ SummarySvc   │  4. LLM → generate summary text      │
│  └──────┬───────┘                                       │
│         │                                               │
│  ┌──────▼───────┐                                       │
│  │ RenameSvc    │  5. LLM → suggest clean filename      │
│  └──────┬───────┘                                       │
│         │                                               │
│  ┌──────▼──────────┐                                    │
│  │ ClassificationSvc│ 6. Score file against each        │
│  │                  │    category using embeddings       │
│  └──────┬───────────┘                                   │
│         │                                               │
│  ┌──────▼───────┐                                       │
│  │ HistorySvc   │  7. Append audit log entry            │
│  └──────┬───────┘                                       │
│         │                                               │
│         └──── OrganizeResponse ──────────────────────▶  │
└─────────────────────────────────────────────────────────┘
```

---

## API Groups

| Group        | Base Path          | Purpose                                    |
|--------------|--------------------|--------------------------------------------|
| **Organize** | `POST /api/organize` | Core pipeline — analyse files             |
| **Categories** | `/api/settings/categories` | CRUD for classification buckets |
| **Settings** | `/api/settings/default-base-path` | Default folder for categories |
| **History**  | `/api/history`     | Read audit log of past organize runs       |
| **Health**   | `GET /health`      | Liveness check                             |

---

## Prerequisite — Categories

Before calling `/api/organize`, you need **at least one active category**.  
Categories are user-defined buckets (e.g. "Invoices", "Code Projects") with:

- A **name** and **description** (used to build an embedding for semantic matching)
- Optional **keywords_text** (freeform EN/TH keywords that boost embedding quality)
- A **destination_path** (where the frontend would ultimately move the file — not used by the worker)
- A **color** (UI only)

> Default categories are seeded when Tauri calls `PUT /api/settings/initial-base-path` — check `GET /api/settings/categories` after that.

---

## Step-by-Step Pipeline Detail

### Step 1 — Scan
`ScannerService.scan("/abs/path/file.pdf")`  
Returns: `original_path`, `file_name`, `extension`, `size_bytes`, `sha256`  
Errors captured (file not found, not readable, outside allowed root) → returned in result.

### Step 2 — Upsert DB record
Creates or updates a `File` row in SQLite (`klin.db`).  
If the SHA-256 changed, the hash/size are updated in place.

### Step 3 — RAG ingest
`RagService.ingest(path)` — only runs if the RAG engine is ready.  
Parses the file, chunks it, and embeds chunks into a local vector store.  
Skipped silently if RAG is not initialised.

### Step 4 — Summarise
`SummaryService.summarise(path)` — calls the local LLM.  
Returns a 1–3 sentence description of the file's content.

### Step 5 — Rename suggestion
`RenameService.suggest_name(original_name, extension, summary)` — calls the LLM.  
Returns a clean, human-readable filename stem (no extension, no spaces).

### Step 6 — Classify
`ClassificationService.classify(file_id, file_path, db, summary)`.  
Embeds the file summary, computes cosine similarity against every active category embedding.  
Returns scores sorted descending — the highest score is `top_category`.

### Step 7 — History log
Appends an `organized` audit entry with the top category, score, and all scores.

---

## Data Flow Diagram

```
Input
  └─ filepaths: ["/Users/you/Downloads/invoice_jan.pdf"]

                    ┌────────────────────────────────────────────┐
                    │           OrganizeFileResult               │
                    ├────────────────────────────────────────────┤
                    │ filepath       "/Users/you/.../invoice..."  │
                    │ file_id        "uuid-v4-..."                │
                    │ analysis                                    │
                    │   summary      "An invoice from Acme Corp…" │
                    │   suggested_name  "2024_acme_invoice_jan"   │
                    │ categories     [{name:"Invoices",score:0.93}│
                    │                 {name:"Contracts",score:0.4}│
                    │                 ...]                        │
                    │ top_category   {name:"Invoices",score:0.93} │
                    │ error          null                         │
                    └────────────────────────────────────────────┘
```

---

## Storage Locations (Dev Mode)

| Item            | Location                          |
|-----------------|-----------------------------------|
| SQLite database | `.storage/klin.db`                |
| Vector store    | `.storage/rag/` (RAG-Anything)    |
| LLM model       | `models/*.gguf`                   |
| Server port     | `http://127.0.0.1:8000`           |
