# Architecture

> Technical deep-dive into Klin-Worker internals — data flow, service design, LLM integration, security model, and future roadmap.

---

## Table of Contents

- [System Overview](#system-overview)
- [Architecture Diagram](#architecture-diagram)
- [Request Lifecycle](#request-lifecycle)
- [Service Layer](#service-layer)
  - [ScannerService](#scannerservice)
  - [RagService](#ragservice)
- [LLM Integration (Ollama)](#llm-integration-ollama)
- [Data Models](#data-models)
- [Security Model](#security-model)
- [Configuration System](#configuration-system)
- [Dependency Injection](#dependency-injection)
- [Future Roadmap](#future-roadmap)

---

## System Overview

Klin-Worker is a **local-only** FastAPI backend designed to run as a sidecar to a Tauri desktop application. The core principle is **privacy-first**: all file processing, LLM inference, and embedding storage happen on the user's machine.

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
     ┌─────┼──────────────────┐
     ▼     ▼                  ▼
┌────────┐ ┌──────────────┐ ┌────────────────┐
│ Local  │ │ RAG-Anything │ │    Ollama      │
│ Files  │ │  (LightRAG)  │ │ localhost:11434│
│ (R/O)  │ └──────┬───────┘ └───────┬────────┘
│        │        │                 │
│        │        ▼                 │
│        │  ┌────────────┐          │
│        │  │ ~/.klin/   │          │
│        │  │ rag_storage│   ◄──────┘
│        │  └────────────┘  embeddings + graph
└────────┘
```

**Key constraints:**

- ❌ No file uploads — only absolute paths
- ❌ No cloud APIs — Ollama runs locally
- ❌ No file mutation — read-only analysis
- ✅ All data stays on the user's machine

---

## Architecture Diagram

### Module Dependency Graph

```
app/main.py
  ├── app/core/config.py          (Settings singleton)
  ├── app/api/organize.py         (Router)
  │     ├── app/models/request.py
  │     ├── app/models/response.py
  │     ├── app/services/scanner_service.py
  │     └── app/services/rag_service.py
  │           ├── raganything        (3rd party)
  │           ├── lightrag           (3rd party)
  │           └── ollama             (3rd party)
  └── app/services/rag_service.py (singleton via lifespan)
```

### Layer Separation

```
┌─────────────────────────────────────────────┐
│                API Layer                     │
│  app/api/organize.py                        │
│  • Route definitions                        │
│  • Request validation (Pydantic)            │
│  • Dependency injection                     │
│  • NO business logic                        │
├─────────────────────────────────────────────┤
│              Service Layer                   │
│  app/services/scanner_service.py            │
│  app/services/rag_service.py                │
│  • All business logic lives here            │
│  • Stateless (Scanner) / Singleton (RAG)    │
│  • Async I/O                                │
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
  "organize": ["/path/to/file.pdf"],
  "rules": { "check_dup": true, "allow_rename": true }
}

     │
     ▼
[1] FastAPI validates request body → OrganizeRequest (Pydantic)
     │
     ▼
[2] Dependency Injection resolves:
     • ScannerService (new instance per request)
     • RagService (singleton, initialised at startup)
     │
     ▼
[3] ScannerService.scan_many(file_paths)
     │
     ├── For each path:
     │   ├── Security check (blocked dirs, allowed roots, path traversal)
     │   ├── Existence + permission check
     │   ├── Extract: file name, extension, size_bytes
     │   └── Compute SHA-256 hash (async, chunked)
     │
     ▼
[4] For each successfully scanned file:
     │
     ├── RagService.ingest(file_path)
     │   └── RAGAnything.process_document_complete()
     │       └── MinerU parses → content blocks
     │           └── LightRAG inserts into knowledge graph
     │               ├── Ollama LLM extracts entities/relations
     │               └── Ollama Embed generates vectors
     │
     ├── RagService.find_duplicates(file_path)  [if check_dup=true]
     │   └── Semantic similarity search against corpus
     │
     ▼
[5] Build response:
     • OrganizeSummary (counts)
     • FileAnalysisResult[] (per-file details)
     │
     ▼
[6] Return OrganizeResponse (JSON)
```

### Response Flow

```json
{
  "summary": {
    "total_files": 1,
    "scanned_ok": 1,
    "duplicates_found": 0,
    "rename_suggestions": 0,
    "errors": 0
  },
  "files": [
    {
      "original_path": "/path/to/file.pdf",
      "status": "ok",
      "duplicate_of": null,
      "suggested_name": null,
      "suggested_category": null,
      "confidence": 0.95,
      "metadata": {
        "original_path": "/path/to/file.pdf",
        "file_name": "file.pdf",
        "extension": ".pdf",
        "size_bytes": 204800,
        "sha256": "a1b2c3d4...",
        "exists": true,
        "error": null
      }
    }
  ]
}
```

---

## Service Layer

### ScannerService

**File:** `app/services/scanner_service.py`  
**Pattern:** Stateless — new instance per request  
**Responsibility:** Local file system metadata extraction

```
Input: absolute file path (string)
  │
  ├─ _check_security()
  │    ├─ path.resolve() — normalize, prevent ../ traversal
  │    ├─ blocked_directories check
  │    └─ allowed_roots check (if configured)
  │
  ├─ Existence + permission check (os.access)
  │
  ├─ Metadata extraction
  │    ├─ file name, extension, size (from stat)
  │    └─ SHA-256 hash (async, chunked via aiofiles)
  │
  Output: FileScanResult
```

**Key design decisions:**

- Never raises exceptions — errors are captured in `FileScanResult.error`
- Async file I/O via `aiofiles` to avoid blocking the event loop
- SHA-256 computed in 8KB chunks for memory efficiency
- Security checks run **before** any file read

---

### RagService

**File:** `app/services/rag_service.py`  
**Pattern:** Singleton — initialised once at startup via lifespan  
**Responsibility:** Semantic analysis through RAG-Anything + Ollama

#### Initialisation Chain

```
app startup (lifespan)
  │
  └─ RagService.setup()
       │
       ├─ Create RAGAnythingConfig(working_dir=~/.klin/rag_storage)
       │
       ├─ Create EmbeddingFunc
       │    └─ ollama_embed(model="embeddinggemma:300m", host=localhost:11434)
       │
       └─ Create RAGAnything(
            config=config,
            llm_model_func=ollama_model_complete,    ← LLM calls
            embedding_func=embedding_func,            ← embedding calls
            lightrag_kwargs={
              llm_model_name: "gemma3:1b",
              llm_model_kwargs: { host, timeout, num_ctx }
            }
          )
```

#### Methods

| Method | Description |
|---|---|
| `setup()` | One-time init. Connects RAG-Anything → LightRAG → Ollama |
| `ingest(file_path)` | Parse + embed a file into the knowledge graph |
| `semantic_search(query)` | Query the corpus for semantically similar content |
| `find_duplicates(file_path)` | Find near-duplicate files (stub, ready for enhancement) |

---

## LLM Integration (Ollama)

### Connection Architecture

```
┌─────────────┐     ┌───────────┐     ┌──────────────┐
│ RagService  │ ──► │ LightRAG  │ ──► │   Ollama     │
│             │     │           │     │ :11434       │
│ setup()     │     │ llm_func  │────►│ gemma3:1b    │
│             │     │ embed_func│────►│ embedding    │
│             │     │           │     │ gemma:300m   │
└─────────────┘     └───────────┘     └──────────────┘
```

### What Each Model Does

| Model | Role | Used For |
|---|---|---|
| `gemma3:1b` | LLM (chat/completion) | Entity extraction, relation mapping, summarization during ingestion. Query answering during search. |
| `embeddinggemma:300m` | Embedding (768-dim) | Converting text chunks into dense vectors for similarity search and duplicate detection. |

### How LightRAG Uses Ollama

During **ingestion** (`process_document_complete`):

1. MinerU parser extracts content blocks from the file (text, tables, images, equations)
2. LightRAG sends each chunk to Ollama LLM for **entity + relation extraction**
3. Entities and relations are stored in a knowledge graph
4. Each chunk is sent to Ollama Embed for **vector embedding**
5. Embeddings are stored in a local nano-vectordb

During **query** (`aquery`):

1. Query text is embedded via Ollama Embed
2. Vector similarity search finds relevant chunks
3. Knowledge graph traversal finds related entities
4. Ollama LLM generates a synthesised answer

### Configuration

All Ollama settings are controlled via environment variables:

```bash
KLIN_OLLAMA_HOST=http://localhost:11434   # Ollama server
KLIN_OLLAMA_LLM_MODEL=gemma3:1b          # Chat model
KLIN_OLLAMA_EMBED_MODEL=embeddinggemma:300m  # Embedding model
KLIN_OLLAMA_EMBEDDING_DIM=768            # Must match model output
KLIN_OLLAMA_MAX_TOKEN_SIZE=2048          # Context window
KLIN_OLLAMA_TIMEOUT=300                  # Request timeout (seconds)
```

### Swapping Models

To use different models, just change the env vars and pull the models:

```bash
# Example: switch to a larger LLM
ollama pull llama3.2:3b
export KLIN_OLLAMA_LLM_MODEL=llama3.2:3b

# Example: switch to nomic-embed-text (also 768-dim)
ollama pull nomic-embed-text
export KLIN_OLLAMA_EMBED_MODEL=nomic-embed-text
```

> ⚠️ If you change the embedding model, you must also update `KLIN_OLLAMA_EMBEDDING_DIM` to match the new model's output dimension, and re-ingest all files (old embeddings become incompatible).

---

## Data Models

### Request

```
OrganizeRequest
├── organize: list[str]         # Absolute file paths (required, min 1)
└── rules: OrganizeRules
    ├── check_dup: bool         # Enable duplicate detection (default: true)
    └── allow_rename: bool      # Allow rename suggestions (default: true)
```

### Response

```
OrganizeResponse
├── summary: OrganizeSummary
│   ├── total_files: int
│   ├── scanned_ok: int
│   ├── duplicates_found: int
│   ├── rename_suggestions: int
│   └── errors: int
└── files: list[FileAnalysisResult]
    ├── original_path: str
    ├── status: "ok" | "duplicate" | "error"
    ├── duplicate_of: str | null
    ├── suggested_name: str | null
    ├── suggested_category: str | null
    ├── confidence: float (0.0–1.0)
    └── metadata: FileScanResult | null
        ├── original_path: str
        ├── file_name: str
        ├── extension: str
        ├── size_bytes: int
        ├── sha256: str
        ├── exists: bool
        └── error: str | null
```

---

## Security Model

### 5-Layer Protection

```
[1] Allowed Root Directories
    │  Only process files under explicitly allowed paths
    │  Configured via KLIN_ALLOWED_ROOTS
    │
[2] Blocked Directories
    │  Hard-reject system-critical paths:
    │  macOS: /System, /Library, /usr, /bin, /sbin, /private
    │  Windows: C:\Windows, C:\Program Files
    │
[3] Path Normalisation
    │  path.resolve() prevents ../ traversal attacks
    │
[4] Permission Check
    │  os.access(path, R_OK) before any file read
    │
[5] Read-Only Operations
       The API NEVER moves, renames, or deletes files
       Only reads for metadata + content analysis
```

### Security Flow in ScannerService

```
file_path (user input)
  │
  ├─ resolve() ─── catches "../../../etc/passwd"
  │
  ├─ blocked check ─── rejects "/System/...", "C:\Windows\..."
  │
  ├─ allowed roots check ─── if configured, rejects paths outside
  │
  ├─ exists? is_file? ─── rejects dirs, symlinks to dangerous targets
  │
  ├─ os.access(R_OK)? ─── rejects unreadable files
  │
  └─ ✅ Safe to read
```

---

## Configuration System

### How Settings Work

```
.env file (or environment variables)
  │
  ▼
pydantic-settings (BaseSettings)
  │  prefix: KLIN_
  │  e.g., KLIN_OLLAMA_HOST → settings.ollama_host
  │
  ▼
app/core/config.py → settings (singleton)
  │
  ▼
Imported by all services
```

### Config Categories

| Category | Keys | Purpose |
|---|---|---|
| **App** | `app_name`, `app_version`, `debug` | General app identity |
| **Server** | `host`, `port` | uvicorn bind address |
| **CORS** | `cors_origins` | Tauri frontend origins |
| **Security** | `allowed_roots`, `blocked_directories` | File system access control |
| **RAG** | `rag_working_dir` | Where RAG-Anything stores data |
| **Ollama** | `ollama_host`, `ollama_llm_model`, `ollama_embed_model`, `ollama_embedding_dim`, `ollama_max_token_size`, `ollama_timeout` | LLM backend config |
| **Duplicates** | `similarity_threshold`, `duplicate_check_enabled` | Semantic duplicate tuning |
| **Queue** _(future)_ | `max_queue_size`, `worker_concurrency` | Background processing |
| **Watcher** _(future)_ | `watch_directories`, `watch_poll_interval_seconds` | File system watcher |
| **Cache** _(future)_ | `embedding_cache_dir`, `embedding_cache_max_items` | Embedding cache |

---

## Dependency Injection

### Pattern

```python
# Route handler receives services via FastAPI Depends()

@router.post("/organize")
async def organize_files(
    body: OrganizeRequest,
    scanner: ScannerService = Depends(get_scanner),  # new per request
    rag: RagService = Depends(get_rag),              # singleton
):
    ...
```

### Service Lifecycles

| Service | Lifecycle | Why |
|---|---|---|
| `ScannerService` | **Per-request** | Stateless, lightweight |
| `RagService` | **Singleton** | Heavy init (loads models), shared state (knowledge graph) |

### Singleton Wiring

```
app/main.py
  │
  ├── _rag_service = RagService()         # module-level instance
  ├── lifespan: await _rag_service.setup()  # init at startup
  └── get_rag_service() → _rag_service    # accessor for DI
        ▲
        │
app/api/organize.py
  └── get_rag() → from app.main import get_rag_service
```

---

## Future Roadmap

Config knobs are already in place for these features:

### 🔍 Semantic Duplicate Detection

```
Current: stub in RagService.find_duplicates()
Plan:    Query embedded corpus for near-duplicates above threshold
Config:  KLIN_SIMILARITY_THRESHOLD=0.85
```

### ⚡ Background Ingestion Queue

```
Current: ingestion is inline (blocks the request)
Plan:    asyncio.Queue or Celery for background processing
Config:  KLIN_MAX_QUEUE_SIZE=1000, KLIN_WORKER_CONCURRENCY=2
```

### 👁 File Watcher Service

```
Current: manual file submission via API
Plan:    watchdog-based directory monitoring, auto-ingest new files
Config:  KLIN_WATCH_DIRECTORIES, KLIN_WATCH_POLL_INTERVAL_SECONDS=5
```

### 💾 Embedding Cache

```
Current: re-embed on every ingest
Plan:    SHA-256 → embedding lookup cache, skip re-processing
Config:  KLIN_EMBEDDING_CACHE_DIR, KLIN_EMBEDDING_CACHE_MAX_ITEMS=10000
```

### 🏷 AI Rename & Categorisation

```
Current: suggested_name and suggested_category return null
Plan:    Use LLM to generate meaningful file names and semantic categories
```

### 📂 Hybrid Routing Engine

```
Current: not implemented
Plan:    Combine semantic similarity + smart rules + user feedback
         to suggest destination folders
```

---

## Storage Layout

```
~/.klin/
└── rag_storage/           # RAG-Anything working directory
    ├── graph_chunk_entity_relation.graphml   # Knowledge graph
    ├── vdb_*              # nano-vectordb files (embeddings)
    ├── kv_store_*          # Key-value caches
    └── ...                # LightRAG internal files
```

This directory is created automatically on first startup. To reset all semantic data, delete `~/.klin/rag_storage/`.

---

## Ports & Networking

| Service | Address | Protocol |
|---|---|---|
| Klin-Worker API | `127.0.0.1:8000` | HTTP |
| Ollama | `127.0.0.1:11434` | HTTP |
| Tauri → Worker | `localhost:8000` | HTTP (CORS-enabled) |

All traffic is **localhost only**. Nothing is exposed to the network.
