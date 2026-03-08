# Klin-Worker

> AI File Organizer — local FastAPI backend that analyses files on your machine using semantic AI.  
> Privacy-first. No file uploads. No cloud dependency. Everything runs locally.

> Current branch runtime: `klin-worker` talks to an external OpenAI-compatible `llama-server` endpoint, usually `http://127.0.0.1:8080/v1`. For app development, you can run `llama-server` from Docker Compose in the app repo and run this worker from source with reload.

---

## What is this?

**Klin-Worker** is the backend service for the AI File Organizer desktop app. It receives absolute file paths from a [Tauri](https://tauri.app/) frontend, scans the files locally, ingests them into a semantic engine ([RAG-Anything](https://github.com/RAG-Anything/RAG-Anything)), and returns a structured analysis — without ever moving, renaming, or deleting anything.

Think of it as a **read-only AI advisor** for your file system. In this branch, inference is handled by a local [llama.cpp server](https://github.com/ggml-org/llama.cpp) exposed through its OpenAI-compatible HTTP API.

---

## Tech Stack

| Layer | Technology |
|---|---|
| API framework | [FastAPI](https://fastapi.tiangolo.com/) |
| Package manager | [uv](https://docs.astral.sh/uv/) |
| LLM backend | [llama.cpp server](https://github.com/ggml-org/llama.cpp) (out-of-process, OpenAI-compatible API) |
| Semantic engine | [RAG-Anything](https://github.com/RAG-Anything/RAG-Anything) → [LightRAG](https://github.com/HKUDS/LightRAG) |
| LLM model | GGUF served by `llama-server` (chat + embeddings, swappable) |
| Database | SQLite (async via aiosqlite) + [SQLModel](https://sqlmodel.tiangolo.com/) |
| Migrations | [Alembic](https://alembic.sqlalchemy.org/) |
| Frontend | Tauri (separate repo) |
| Python | 3.13+ |

---

## Prerequisites

1. **Python 3.13+**
2. **[uv](https://docs.astral.sh/uv/getting-started/installation/)** — fast Python package manager
3. **A GGUF model file** for `llama-server` — for example `Qwen2.5-VL-3B-Instruct-IQ4_XS.gguf` (see below)

---

## Quick Start

### 1. Download the GGUF model

A single model can handle both chat and embeddings. Place it where your chosen runtime can access it:

- app-dev Docker Compose flow: **klin-app/models**
- packaged sidecar flow: any path referenced by `KLIN_MODEL_PATH`

```bash
mkdir -p models
// Download the model and save it to klin-app/models/your-model.gguf for Docker Compose dev
// You can use any GGUF model with chat + embedding capabilities — just update the matching env value
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
# Development (auto-reload enabled)
uv run fastapi dev app/main.py

# Production
uv run fastapi run app/main.py
```

The server starts at `http://127.0.0.1:8000`. On first boot it will:
- Run database migrations (creates `.storage/klin.db`)
- Load the GGUF model into memory
- Wait for `PUT /api/settings/initial-base-path` (called by Tauri) to seed 12 default categories with folder paths and embeddings

### 5. Verify

```bash
# Health check
curl http://127.0.0.1:8000/health

# Should return:
# {"status":"ok","version":"0.2.0","rag_ready":true}

# List auto-seeded categories
curl http://127.0.0.1:8000/api/settings/categories
```

---

## Environment Variables

All variables are prefixed with `KLIN_`. See `.env.example` for the full list.

| Variable | Default | Description |
|---|---|---|
| `KLIN_LLAMA_SERVER_URL` | `http://127.0.0.1:8080/v1` | OpenAI-compatible `llama-server` base URL |
| `KLIN_EMBEDDING_DIM_SIZE` | `2048` | Embedding vector dimension expected from the served model |
| `KLIN_MAX_TOKEN_LIMIT` | `4096` | Max token budget used by worker generation / chunking |
| `KLIN_DEBUG` | `false` | Enable debug logging |
| `KLIN_RAG_WORKING_DIR` | `.storage/rag_storage` in source-run dev | RAG-Anything storage path |
| `KLIN_DATABASE_PATH` | `.storage/klin.db` in source-run dev | SQLite database path |
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
│   │   ├── settings/                    # Settings sub-routers
│   │   │   ├── __init__.py              # Combines routers under /api/settings
│   │   │   ├── categories.py            # CRUD /api/settings/categories
│   │   │   ├── base_path.py             # GET/PUT /api/settings/default-base-path
│   │   │   └── init_base_path.py        # PUT /api/settings/initial-base-path
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
