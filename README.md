# Klin-Worker

> AI File Organizer — local FastAPI backend that analyses files on your machine using semantic AI.  
> Privacy-first. No file uploads. No cloud dependency. Everything runs locally.

---

## What is this?

**Klin-Worker** is the backend service for the AI File Organizer desktop app. It receives absolute file paths from a [Tauri](https://tauri.app/) frontend, scans the files locally, ingests them into a semantic engine ([RAG-Anything](https://github.com/RAG-Anything/RAG-Anything)), and returns a structured analysis — without ever moving, renaming, or deleting anything.

Think of it as a **read-only AI advisor** for your file system. Powered by [llama-cpp-python](https://github.com/abetlen/llama-cpp-python) for fully local, in-process inference — no external server needed.

---

## Tech Stack

| Layer | Technology |
|---|---|
| API framework | [FastAPI](https://fastapi.tiangolo.com/) |
| Package manager | [uv](https://docs.astral.sh/uv/) |
| LLM backend | [llama-cpp-python](https://github.com/abetlen/llama-cpp-python) (in-process GGUF) |
| Semantic engine | [RAG-Anything](https://github.com/RAG-Anything/RAG-Anything) → [LightRAG](https://github.com/HKUDS/LightRAG) |
| LLM model | `gemma-3-1b-it-Q4_K_M.gguf` (chat + embeddings, swappable) |
| Database | SQLite (async via aiosqlite) + [SQLModel](https://sqlmodel.tiangolo.com/) |
| Migrations | [Alembic](https://alembic.sqlalchemy.org/) |
| Frontend | Tauri (separate repo) |
| Python | 3.13+ |

---

## Prerequisites

1. **Python 3.13+**
2. **[uv](https://docs.astral.sh/uv/getting-started/installation/)** — fast Python package manager
3. **A GGUF model file** — e.g. `gemma-3-1b-it-Q4_K_M.gguf` (see below)

---

## Quick Start

### 1. Download the GGUF model

A single model handles both chat and embeddings — no external server needed.

```bash
mkdir -p models
// Download the model and save it to `models/gemma-3-1b-it-Q4_K_M.gguf`
// You can use any GGUF model with chat + embedding capabilities — just update the path in .env
TBD: Add mirror links for popular models (Gemma, Mistral, Falcon)
```

### 2. Clone & install dependencies

```bash
git clone <repo-url>
cd klin-worker
uv sync
```

### 3. Configure environment (optional)

```bash
cp .env.example .env
# Edit .env if you want to change model path, context size, or thresholds
```

### 4. Start the server

```bash
uv run uvicorn app.main:app --reload
```

The server starts at `http://127.0.0.1:8000`. On first boot it will:
- Run database migrations (creates `.storage/klin.db`)
- Load the GGUF model into memory
- Seed 12 default file categories with embeddings

### 5. Verify

```bash
# Health check
curl http://127.0.0.1:8000/health

# Should return:
# {"status":"ok","version":"0.2.0","rag_ready":true}

# List auto-seeded categories
curl http://127.0.0.1:8000/api/categories
```

---

## Environment Variables

All variables are prefixed with `KLIN_`. See `.env.example` for the full list.

| Variable | Default | Description |
|---|---|---|
| `KLIN_LLAMACPP_MODEL_PATH` | `models/gemma-3-1b-it-Q4_K_M.gguf` | Path to GGUF model file |
| `KLIN_LLAMACPP_N_CTX` | `2048` | Context window size |
| `KLIN_LLAMACPP_N_GPU_LAYERS` | `0` | GPU layers (-1 = all) |
| `KLIN_LLAMACPP_EMBEDDING_DIM` | `2048` | Embedding vector dimension |
| `KLIN_LLAMACPP_MAX_TOKEN_SIZE` | `2048` | Max tokens for generation |
| `KLIN_LLAMACPP_VERBOSE` | `false` | Enable llama.cpp verbose output |
| `KLIN_DEBUG` | `false` | Enable debug logging |
| `KLIN_RAG_WORKING_DIR` | `~/.klin/rag_storage` | RAG-Anything storage path |
| `KLIN_SIMILARITY_THRESHOLD` | `0.85` | Duplicate detection threshold |

---

## Project Structure

```
klin-worker/
├── main.py                              # Convenience launcher
├── pyproject.toml                       # uv dependencies
├── alembic.ini                          # Alembic migration config
├── .env.example                         # Environment template
├── models/                              # GGUF model files (git-ignored)
│   └── gemma-3-1b-it-Q4_K_M.gguf
├── app/
│   ├── main.py                          # FastAPI app, CORS, lifespan
│   ├── api/
│   │   ├── organize.py                  # POST /api/organize
│   │   ├── categories.py               # CRUD /api/categories
│   │   └── history.py                   # GET /api/history
│   ├── services/
│   │   ├── llm_client.py               # llama-cpp-python singleton wrapper
│   │   ├── rag_service.py              # RAG-Anything + LightRAG wrapper
│   │   ├── scanner_service.py          # File validation + metadata
│   │   ├── classification_service.py   # Cosine-similarity scoring
│   │   ├── summary_service.py          # AI file summaries
│   │   ├── rename_service.py           # AI filename suggestions
│   │   ├── seed_service.py             # Default category seeding
│   │   ├── startup_checks.py           # Health checks at boot
│   │   └── history_service.py          # Audit log
│   ├── models/
│   │   ├── request.py                   # Request schemas
│   │   └── response.py                 # Response schemas
│   ├── db/
│   │   ├── models.py                    # SQLModel ORM (5 tables)
│   │   ├── session.py                   # Async engine + get_db
│   │   └── migrations.py               # Alembic runner
│   └── core/
│       └── config.py                    # Centralized settings
├── alembic/
│   └── versions/                        # DB migration scripts
├── docs/
│   ├── ONBOARDING.md                    # Developer onboarding index
│   └── onboarding/                      # Step-by-step guide (7 parts)
└── ai/
    └── init.md                          # Initial design document
```

---

## License

TBD
