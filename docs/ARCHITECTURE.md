# Architecture

> Technical deep-dive into Klin-Worker internals — data flow, service design, LLM integration, database schema, security model, and future roadmap.

---

## Table of Contents

- [System Overview](#system-overview)
- [Architecture Diagram](#architecture-diagram)
- [Request Lifecycle](#request-lifecycle)
- [Database Schema](#database-schema)
- [Service Layer](#service-layer)
  - [ScannerService](#scannerservice)
  - [RagService](#ragservice)
  - [ClassificationService](#classificationservice)
  - [SummaryService](#summaryservice)
  - [RenameService](#renameservice)
  - [HistoryService](#historyservice)
- [LLM Integration (llama-cpp-python)](#llm-integration-llama-cpp-python)
- [Data Models](#data-models)
- [Security Model](#security-model)
- [Configuration System](#configuration-system)
- [Dependency Injection](#dependency-injection)
- [Future Roadmap](#future-roadmap)

---

## System Overview

Klin-Worker is a **local-only** FastAPI backend designed to run as a sidecar to a Tauri desktop application. It functions as an **AI Workspace** — users define categories, and the system classifies, summarises, and suggests renames for files using local AI models.

The core principle is **privacy-first**: all file processing, LLM inference, embedding storage, and user data live on the user's machine.

```
┌──────────────────────┐
│   Tauri Frontend     │
│   (Desktop App)      │
└──────────┬───────────┘
           │  HTTP (localhost)
           ▼
┌──────────────────────┐
│   FastAPI Server     │ ← klin-worker (this project)
│   127.0.0.1:8000     │
└──────────┬───────────┘
           │
     ┌─────┼──────────┬──────────────────┐
     ▼     ▼          ▼                  ▼
┌────────┐ ┌────────┐ ┌──────────────┐ ┌────────────────┐
│ Local  │ │ SQLite │ │ RAG-Anything │ │ llama-cpp-python │
│ Files  │ │ ~/.klin│ │  (LightRAG)  │ │ (in-process GGUF)│
│ (R/O)  │ │/klin.db│ └──────┬───────┘ └────────┬─────────┘
│        │ └────────┘        │                  │
│        │                   ▼                  │
│        │            ┌────────────┐            │
│        │            │ ~/.klin/   │            │
│        │            │ rag_storage│   ◄────────┘
│        │            └────────────┘  embeddings + graph
└────────┘
```

**Key constraints:**

- ❌ No file uploads — only absolute paths
- ❌ No cloud APIs — llama-cpp-python runs in-process
- ❌ No file mutation — read-only analysis
- ✅ All data stays on the user's machine
- ✅ Persistent user categories in SQLite
- ✅ Full audit history of all operations

---

## Architecture Diagram

### Module Dependency Graph

```
app/main.py
  ├── app/core/config.py              (Settings singleton)
  ├── app/db/session.py               (SQLite async engine)
  ├── app/db/migrations.py            (Table creation)
  ├── app/api/organize.py            (Router — POST /api/organize)
  │     ├── app/models/request.py
  │     ├── app/models/response.py
  │     ├── app/services/scanner_service.py
  │     ├── app/services/classification_service.py
  │     ├── app/services/summary_service.py
  │     ├── app/services/rename_service.py
  │     ├── app/services/history_service.py
  │     └── app/services/rag_service.py
  ├── app/api/settings/              (Router — /api/settings/*)
  │     ├── categories.py             (CRUD /api/settings/categories)
  │     ├── base_path.py              (GET/PUT /api/settings/default-base-path)
  │     └── init_base_path.py         (PUT /api/settings/initial-base-path)
  │     └── app/services/seed_service.py  (← _build_embed_text shared)
  ├── app/api/history.py              (Router — GET /api/history)
  ├── app/services/seed_service.py    (Seeding — called via initial-base-path endpoint)
  └── app/services/startup_checks.py  (Startup — verifies DB, llama-cpp-python, RAG)
```

### Layer Separation

```
┌─────────────────────────────────────────────┐
│                API Layer                     │
│  app/api/organize.py                        │
│  app/api/settings/categories.py              │
│  app/api/settings/base_path.py               │
│  app/api/settings/init_base_path.py          │
│  app/api/history.py                         │
│  • Route definitions                        │
│  • Request validation (Pydantic)            │
│  • Dependency injection                     │
│  • NO business logic                        │
├─────────────────────────────────────────────┤
│              Service Layer                   │
│  scanner_service.py    — file metadata      │
│  classification_service.py — AI scoring     │
│  summary_service.py    — AI summaries       │
│  rename_service.py     — AI rename          │
│  history_service.py    — audit log          │
│  rag_service.py        — embeddings + RAG   │
│  • All business logic lives here            │
│  • Async I/O throughout                     │
├─────────────────────────────────────────────┤
│              Data Layer                      │
│  app/db/models.py      — SQLAlchemy ORM     │
│  app/db/session.py     — Async engine       │
│  app/db/migrations.py  — Schema bootstrap   │
│  • SQLite local database                    │
│  • Async via aiosqlite                      │
├─────────────────────────────────────────────┤
│              Model Layer                     │
│  app/models/request.py                      │
│  app/models/response.py                     │
│  • Pydantic schemas                         │
│  • Type-safe, validated, documented         │
├─────────────────────────────────────────────┤
│              Core Layer                      │
│  app/core/config.py                         │
│  • pydantic-settings                        │
│  • Environment variables (KLIN_ prefix)     │
│  • Singleton Settings instance              │
└─────────────────────────────────────────────┘
```

---

## Request Lifecycle

### `POST /api/organize` — Step by Step

```
Client sends:
{
  "filepaths": ["/path/to/file.pdf"]
}

     │
     ▼
[1] FastAPI validates request body → OrganizeRequest (Pydantic)
     │
     ▼
[2] Dependency Injection resolves:
     • ScannerService (new per request)
     • RagService (singleton)
     • ClassificationService (new per request, wraps RagService)
     • SummaryService (new per request, wraps RagService)
     • RenameService (new per request)
     • HistoryService (new per request)
     • AsyncSession (scoped DB session)
     │
     ▼
[3] For each filepath:
     │
     ├── ScannerService.scan(path)
     │   ├── Security check
     │   ├── Existence + permission check
     │   ├── Extract: name, extension, size
     │   └── Compute SHA-256 hash
     │
     ├── Upsert File record in SQLite
     │
     ├── RagService.ingest(path)
     │   └── RAGAnything.process_document_complete()
     │
     ├── SummaryService.summarise(path)
     │   └── llama-cpp-python → one-paragraph summary
     │
     ├── RenameService.suggest_name(name, ext, summary)
     │   └── llama-cpp-python → descriptive filename
     │
     ├── Store FileAnalysis in SQLite
     │
     ├── ClassificationService.classify(file_id, path)
     │   ├── Embed file content
     │   ├── Load category embeddings from DB
     │   ├── Cosine similarity for each category
     │   └── Store CategoryScores in SQLite
     │
     ├── HistoryService.log("categorized", metadata)
     │
     └── Build OrganizeFileResult
     │
     ▼
[4] Return OrganizeResponse (JSON)
```

### Response Example

```json
{
  "results": [
    {
      "filepath": "/Users/sarun/Downloads/doc1.pdf",
      "file_id": "uuid",
      "analysis": {
        "summary": "This document is about traditional Chinese medicine curriculum.",
        "suggested_name": "Traditional_Chinese_Medicine_CM67.pdf"
      },
      "categories": [
        {
          "category_id": "uuid",
          "name": "Education & Learning",
          "score": 0.82
        },
        {
          "category_id": "uuid",
          "name": "Documents",
          "score": 0.41
        }
      ],
      "top_category": {
        "category_id": "uuid",
        "name": "Education & Learning",
        "score": 0.82,
        "destination_path": "/Users/sarun/Pictures/Education & Learning"
      }
    }
  ]
}
```

---

## Database Schema

### ER Diagram

```
Categories
    │
    │ 1
    │
    └──────< CategoryScores >──────┐
                                    │
                                    │
Files ─────< FileAnalysis (1:1) >───┘
    │
    └──────< HistoryLogs >
```

### Tables

#### categories

| Column           | Type     | Notes                    |
|------------------|----------|--------------------------|
| id               | TEXT PK  | UUID                     |
| name             | TEXT     | Unique                   |
| description      | TEXT     |                          |
| color            | TEXT     | Hex (#6366f1)            |
| destination_path | TEXT     | Nullable                 |
| embedding        | TEXT     | JSON-serialised float[]  |
| is_active        | BOOLEAN  |                          |
| created_at       | DATETIME |                          |
| updated_at       | DATETIME |                          |

#### files

| Column        | Type     | Notes    |
|---------------|----------|----------|
| id            | TEXT PK  | UUID     |
| original_path | TEXT     | Unique   |
| hash          | TEXT     | SHA-256  |
| size          | INTEGER  |          |
| extension     | TEXT     |          |
| created_at    | DATETIME |          |

#### file_analysis

| Column         | Type     | Notes              |
|----------------|----------|--------------------|
| id             | TEXT PK  | UUID               |
| file_id        | TEXT FK  | → files.id (1:1)   |
| summary        | TEXT     | AI-generated       |
| suggested_name | TEXT     | AI-generated       |
| processed_at   | DATETIME |                    |

#### category_scores

| Column      | Type     | Notes              |
|-------------|----------|--------------------|
| id          | TEXT PK  | UUID               |
| file_id     | TEXT FK  | → files.id         |
| category_id | TEXT FK  | → categories.id    |
| score       | FLOAT    | Cosine similarity  |

#### history_logs

| Column        | Type     | Notes                |
|---------------|----------|----------------------|
| id            | TEXT PK  | UUID                 |
| file_id       | TEXT FK  | → files.id           |
| action        | TEXT     | e.g. "categorized"   |
| metadata_json | TEXT     | JSON string          |
| created_at    | DATETIME |                      |

---

## Service Layer

### ScannerService

**File:** `app/services/scanner_service.py`  
**Pattern:** Stateless — new instance per request  
**Responsibility:** Local file system metadata extraction

Unchanged from v1 — still handles security checks, existence validation,
and async SHA-256 hashing. Errors are captured in `FileScanResult.error`,
never raised.

---

### RagService

**File:** `app/services/rag_service.py`  
**Pattern:** Singleton — initialised once at startup  
**Responsibility:** Semantic analysis + embedding generation

**New in v2:** Exposes `embed_texts(texts)` for generating raw embedding
vectors used by ClassificationService.

| Method | Description |
|---|---|
| `setup()` | One-time init. Connects RAG-Anything → LightRAG → llama-cpp-python |
| `embed_texts(texts)` | Generate embedding vectors for arbitrary text |
| `ingest(file_path)` | Parse + embed a file into the knowledge graph |
| `semantic_search(query)` | Query the corpus for semantically similar content |
| `find_duplicates(file_path)` | Stub for near-duplicate detection |

---

### ClassificationService

**File:** `app/services/classification_service.py`  
**Pattern:** Per-request, wraps RagService  
**Responsibility:** Score files against user-defined categories

```
File embedding (from RagService.embed_texts)
      ↕ cosine similarity
Category embedding (from DB)
      → score per category
      → sorted, top-K returned
      → persisted to category_scores table
```

Also provides `generate_category_embedding(text)` for embedding
category descriptions when they are created or updated.

---

### SummaryService

**File:** `app/services/summary_service.py`  
**Pattern:** Per-request, wraps RagService  
**Responsibility:** Generate one-paragraph file summaries via llama-cpp-python

---

### RenameService

**File:** `app/services/rename_service.py`  
**Pattern:** Per-request, stateless  
**Responsibility:** Generate descriptive filename suggestions via llama-cpp-python

---

### HistoryService

**File:** `app/services/history_service.py`  
**Pattern:** Per-request, stateless  
**Responsibility:** Append-only audit log in SQLite

---

### SeedService

**File:** `app/services/seed_service.py`  
**Pattern:** Called by `PUT /api/settings/initial-base-path` on first launch (idempotent)  
**Responsibility:** Insert 12 default categories with rich keywords (EN + TH). Also provides `generate_missing_embeddings()` and `_build_embed_text()` shared by the categories router.

> **Note:** Seeding no longer happens automatically at startup. The Tauri frontend
> calls `PUT /api/settings/initial-base-path` after startup, which triggers seeding
> with the correct OS-specific base path so every category gets a `destination_path`
> from the start.

---

### StartupChecks

**File:** `app/services/startup_checks.py`  
**Pattern:** Run-once at startup, results cached for `/health`  
**Responsibility:** Diagnostic checks for Database (SQLite connectivity + schema), llama-cpp-python (model loaded in-process), and RAG-Anything (initialised). Logs a summary table and exposes results via the `/health` endpoint.

---

## LLM Integration (llama-cpp-python)

### In-Process Architecture

The GGUF model is loaded directly into the FastAPI process via `llama-cpp-python`.
No external server is required — all inference happens in-process.

```
┌─────────────────┐     ┌───────────┐     ┌──────────────────┐
│ RagService      │ ──► │ LightRAG  │     │  llama-cpp-python│
│ SummaryService  │ ──► │           │ ──► │  (in-process)    │
│ RenameService   │ ──► │ llm_func  │────►│  gemma-3-1b-it   │
│ Classification  │     │ embed_func│────►│  Q4_K_M.gguf     │
│   Service       │     └───────────┘     └──────────────────┘
└─────────────────┘           │
                              ▼
                     ┌────────────────┐
                     │   LlmClient    │  ← singleton, loaded once at startup
                     │  (llm_client)  │
                     └────────────────┘
```

### Model Capabilities

| Capability | Method | Used For |
|---|---|---|
| Chat completion | `LlmClient.achat()` | Entity extraction, summarisation, rename suggestions, RAG query answering |
| Embedding | `LlmClient.aembed()` | File content vectors, category description vectors, cosine similarity |

---

## Data Models

### Request — POST /api/organize

```
OrganizeRequest
└── filepaths: list[str]     # Absolute file paths (required, min 1)
```

### Response — POST /api/organize

```
OrganizeResponse
└── results: list[OrganizeFileResult]
    ├── filepath: str
    ├── file_id: str (UUID)
    ├── analysis: FileAnalysisResponse
    │   ├── summary: str | null
    │   └── suggested_name: str | null
    ├── categories: list[CategoryScoreResponse]
    │   ├── category_id: str
    │   ├── name: str
    │   └── score: float
    ├── top_category: TopCategoryResponse | null
    │   ├── category_id: str
    │   ├── name: str
    │   ├── score: float
    │   └── destination_path: str | null
    └── error: str | null
```

### Request — Category CRUD

```
CategoryCreate
├── name: str
├── description: str
├── color: str (#hex)
└── destination_path: str | null

CategoryUpdate  (partial)
├── name: str | null
├── description: str | null
├── color: str | null
├── destination_path: str | null
└── is_active: bool | null
```

---

## Security Model

Unchanged from v1 — 5-layer protection:

1. Allowed root directories
2. Blocked system directories
3. Path normalisation (resolve)
4. Permission check (os.access)
5. Read-only operations only

---

## Configuration System

### How Settings Work

```
.env file (or environment variables)
  │
  ▼
pydantic-settings (BaseSettings)
  │  prefix: KLIN_
  │  e.g., KLIN_DATABASE_PATH → settings.database_path
  │
  ▼
app/core/config.py → settings (singleton)
  │
  ▼
Imported by all services
```

### New Config Keys (v2)

| Variable | Default | Description |
|---|---|---|
| `KLIN_DATABASE_PATH` | `~/.klin/klin.db` | SQLite database file path |
| `KLIN_LLAMACPP_MODEL_PATH` | `models/gemma-3-1b-it-Q4_K_M.gguf` | Path to GGUF model file |
| `KLIN_LLAMACPP_N_CTX` | `2048` | Context window size |
| `KLIN_LLAMACPP_N_GPU_LAYERS` | `0` | GPU layers (-1 = offload all) |
| `KLIN_LLAMACPP_EMBEDDING_DIM` | `2048` | Embedding vector dimension |
| `KLIN_LLAMACPP_MAX_TOKEN_SIZE` | `2048` | Max tokens for generation |
| `KLIN_CLASSIFICATION_TOP_K` | `5` | Max categories returned per file |

---

## Dependency Injection

### Pattern

```python
@router.post("/organize")
async def organize_files(
    body: OrganizeRequest,
    db: AsyncSession = Depends(get_db),          # scoped session
    scanner: ScannerService = Depends(…),        # per request
    rag: RagService = Depends(…),                # singleton
    classifier: ClassificationService = Depends(…),  # per request
    summary_svc: SummaryService = Depends(…),    # per request
    rename_svc: RenameService = Depends(…),      # per request
    history_svc: HistoryService = Depends(…),    # per request
):
    ...
```

### Service Lifecycles

| Service | Lifecycle | Why |
|---|---|---|
| `ScannerService` | **Per-request** | Stateless, lightweight |
| `RagService` | **Singleton** | Heavy init (loads models), shared state |
| `ClassificationService` | **Per-request** | Wraps RagService, lightweight |
| `SummaryService` | **Per-request** | Wraps RagService, lightweight |
| `RenameService` | **Per-request** | Stateless |
| `HistoryService` | **Per-request** | Stateless |
| `AsyncSession` | **Per-request** | Scoped DB session |

---

## Future Roadmap

### 📅 Calendar Integration

```
Table: calendar_events
  - id, file_id, title, date, location, raw_payload
  
Integration: Google Calendar API (future)
```

### ⚡ Background Ingestion Queue

```
Config:  KLIN_MAX_QUEUE_SIZE=1000, KLIN_WORKER_CONCURRENCY=2
```

### 👁 File Watcher Service

```
Config:  KLIN_WATCH_DIRECTORIES, KLIN_WATCH_POLL_INTERVAL_SECONDS=5
```

### 💾 Embedding Cache

```
Config:  KLIN_EMBEDDING_CACHE_DIR, KLIN_EMBEDDING_CACHE_MAX_ITEMS=10000
```

### 📂 Hybrid Routing Engine

```
Combine semantic similarity + smart rules + user feedback
to suggest destination folders
```

---

## Storage Layout

```
~/.klin/
├── klin.db                # SQLite database (categories, files, history)
└── rag_storage/           # RAG-Anything working directory
    ├── graph_chunk_entity_relation.graphml
    ├── vdb_*              # nano-vectordb files
    ├── kv_store_*
    └── ...
```

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check |
| `POST` | `/api/organize` | Analyse & classify files |
| `GET` | `/api/settings/categories` | List categories |
| `POST` | `/api/settings/categories` | Create category |
| `GET` | `/api/settings/categories/{id}` | Get category |
| `PATCH` | `/api/settings/categories/{id}` | Update category |
| `DELETE` | `/api/settings/categories/{id}` | Delete category |
| `GET` | `/api/settings/default-base-path` | Get default base path |
| `PUT` | `/api/settings/default-base-path` | Set default base path |
| `PUT` | `/api/settings/initial-base-path` | Startup: set base path + seed categories |
| `GET` | `/api/history` | Recent history |
| `GET` | `/api/history/file/{id}` | File history |

---

## Ports & Networking

| Service | Address | Protocol |
|---|---|---|
| Klin-Worker API | `127.0.0.1:8000` | HTTP |
| llama-cpp-python | in-process | N/A (loaded in FastAPI process) |
| Tauri → Worker | `localhost:8000` | HTTP (CORS-enabled) |

All traffic is **localhost only**. Nothing is exposed to the network.
