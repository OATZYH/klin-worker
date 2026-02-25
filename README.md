# Klin-Worker

> AI File Organizer — local FastAPI backend that analyses files on your machine using semantic AI.  
> Privacy-first. No file uploads. No cloud dependency. Everything runs locally.

---

## What is this?

**Klin-Worker** is the backend service for the AI File Organizer desktop app. It receives absolute file paths from a [Tauri](https://tauri.app/) frontend, scans the files locally, ingests them into a semantic engine ([RAG-Anything](https://github.com/RAG-Anything/RAG-Anything)), and returns a structured analysis — without ever moving, renaming, or deleting anything.

Think of it as a **read-only AI advisor** for your file system.

---

## Tech Stack

| Layer | Technology |
|---|---|
| API framework | [FastAPI](https://fastapi.tiangolo.com/) |
| Package manager | [uv](https://docs.astral.sh/uv/) |
| LLM backend | [Ollama](https://ollama.com/) (local) |
| Semantic engine | [RAG-Anything](https://github.com/RAG-Anything/RAG-Anything) → [LightRAG](https://github.com/HKUDS/LightRAG) |
| LLM model | `gemma3:1b` (default, swappable) |
| Embedding model | `embeddinggemma:300m` (768-dim) |
| Frontend | Tauri (separate repo) |
| Python | 3.13+ |

---

## Prerequisites

1. **Python 3.13+**
2. **[uv](https://docs.astral.sh/uv/getting-started/installation/)** — fast Python package manager
3. **[Ollama](https://ollama.com/download)** — local LLM runtime

---

## Quick Start

### 1. Install Ollama models

```bash
ollama pull gemma3:1b
ollama pull embeddinggemma:300m
```

Verify they're available:

```bash
ollama list
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
# Edit .env if you want to change models, host, or thresholds
```

### 4. Start the server

```bash
uv run uvicorn app.main:app --reload
```

The server starts at `http://127.0.0.1:8000`.

### 5. Verify

```bash
# Health check
curl http://127.0.0.1:8000/health

# Should return:
# {"status":"ok","version":"0.1.0","rag_ready":true}
```

---

## Environment Variables

All variables are prefixed with `KLIN_`. See `.env.example` for the full list.

| Variable | Default | Description |
|---|---|---|
| `KLIN_OLLAMA_HOST` | `http://localhost:11434` | Ollama server URL |
| `KLIN_OLLAMA_LLM_MODEL` | `gemma3:1b` | LLM model for reasoning |
| `KLIN_OLLAMA_EMBED_MODEL` | `embeddinggemma:300m` | Embedding model |
| `KLIN_OLLAMA_EMBEDDING_DIM` | `768` | Embedding vector dimension |
| `KLIN_OLLAMA_TIMEOUT` | `300` | Ollama request timeout (seconds) |
| `KLIN_DEBUG` | `false` | Enable debug logging |
| `KLIN_RAG_WORKING_DIR` | `~/.klin/rag_storage` | RAG-Anything storage path |
| `KLIN_SIMILARITY_THRESHOLD` | `0.85` | Duplicate detection threshold |

---

## Project Structure

```
klin-worker/
├── main.py                         # Convenience launcher
├── pyproject.toml                   # uv dependencies
├── .env.example                     # Environment template
├── app/
│   ├── main.py                      # FastAPI app, CORS, lifespan
│   ├── api/
│   │   └── organize.py              # POST /api/organize
│   ├── services/
│   │   ├── scanner_service.py       # File validation + metadata
│   │   └── rag_service.py           # RAG-Anything + Ollama wrapper
│   ├── models/
│   │   ├── request.py               # Request schemas
│   │   └── response.py              # Response schemas
│   └── core/
│       └── config.py                # Centralized settings
└── ai/
    └── init.md                      # Initial design document
```

---

## License

TBD
