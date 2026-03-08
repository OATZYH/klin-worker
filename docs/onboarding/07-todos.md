# 📋 Tasks Still Not Finished / Improvements

## 🔴 High Priority

| Task | File(s) | What Needs to Happen |
|---|---|---|
| ~~**File content embedding**~~ | ~~`classification_service.py`~~ | ✅ **DONE** — `classify()` now accepts `summary` param. Embedding uses `"filename .ext. <AI summary>"` instead of filename-only. |
| ~~**Category score upsert**~~ | ~~`classification_service.py`~~ | ✅ **DONE** — `_save_scores()` now deletes old scores before inserting new ones. |
| **Duplicate detection** | `rag_service.py` `find_duplicates()` | Currently a stub (`return []`). Should use semantic search to find files with similar content. |
| ~~**`ollama.chat()` is synchronous**~~ | ~~`summary_service.py`, `rename_service.py`~~ | ✅ **DONE** — Migrated to in-process `llama-cpp-python` via `LlmClient.achat()`. |
| **Error handling in organize pipeline** | `organize.py` `_process_single_file()` | If RAG ingest fails, it silently continues. Partial failures need better error reporting. |

## 🟡 Medium Priority

| Task | File(s) | What Needs to Happen |
|---|---|---|
| **Background ingestion queue** | `config.py` has `max_queue_size`, `worker_concurrency` | Config exists but no implementation. Large file batches should be queued and processed in background workers. |
| **File watcher** | `config.py` has `watch_directories`, `watch_poll_interval_seconds` | Config exists but no implementation. Should auto-detect new files in watched directories. |
| **Embedding cache** | `config.py` has `embedding_cache_dir`, `embedding_cache_max_items` | Config exists but no implementation. Repeated embeddings of the same text waste LLM calls. |
| **README is outdated** | `README.md` | Project structure section doesn't list all files. Version shows `0.1.0` in health check example but code is `0.2.0`. Doesn't mention seed categories. |

## 🟢 Nice to Have

| Task | Description |
|---|---|
| **Unit tests** | No tests exist! Add pytest + httpx for API tests, mock llama-cpp-python for service tests. |
| **Rate limiting** | No rate limiting on API endpoints. |
| **Batch processing** | `scanner_service.scan_many()` is sequential. Could use `asyncio.gather()` for parallel scanning. |
| **Better RAG result formatting** | `rag_service._format_results()` handles multiple return shapes — could be more robust. |
| **LLM prompt tuning** | Summary and rename prompts could be improved for better output quality. |
| **Structured LLM output** | Use llama-cpp-python's grammar/JSON schema for rename suggestions instead of regex cleanup. |
| **WebSocket progress** | Long organize requests have no progress feedback to the frontend. |
| **Docker / containerization** | No Dockerfile for easy deployment. |
| **CI/CD pipeline** | No GitHub Actions or similar. |

---

## Quick Start for Development

```bash
# 1. Download GGUF model (no external server needed!)
mkdir -p models && wget -O models/gemma-3-1b-it-Q4_K_M.gguf \
  https://huggingface.co/google/gemma-3-1b-it-GGUF/resolve/main/gemma-3-1b-it-Q4_K_M.gguf

# 2. Install dependencies
uv sync

# 3. Start the server
uv run fastapi dev app/main.py

# 4. Test health check
curl http://127.0.0.1:8000/health

# 5. Set default base path (seeds categories if the DB is empty)
curl -X PUT http://127.0.0.1:8000/api/settings/default-base-path \
  -H "Content-Type: application/json" \
  -d '{"default_base_path": "/Users/you/Documents/KlinFiles"}'

# 6. List categories (seeded by the step above)
curl http://127.0.0.1:8000/api/settings/categories

# 6. Organize a file
curl -X POST http://127.0.0.1:8000/api/organize \
  -H "Content-Type: application/json" \
  -d '{"filepaths": ["/path/to/some/file.pdf"]}'

# 7. Interactive API docs
open http://127.0.0.1:8000/docs
```

---

## Glossary

| Term | Meaning |
|---|---|
| **Embedding** | A list of numbers representing the meaning of text. Similar texts → similar numbers. |
| **Vector** | Same as embedding — a list of numbers. |
| **Cosine Similarity** | Math formula to compare two vectors. Returns 0-1 (1 = identical). |
| **RAG** | Retrieval-Augmented Generation — give the LLM your own documents as context. |
| **Knowledge Graph** | A network of entities and relationships extracted from documents by LightRAG. |
| **LLM** | Large Language Model — the AI brain (gemma3:1b in this project). |
| **llama-cpp-python** | Python bindings for llama.cpp that load GGUF models directly in-process. No external server needed. |
| **Ingestion** | Feeding a document into the RAG engine so it can be searched later. |
| **Sidecar** | A background process bundled with a desktop app (Tauri launches klin-worker as a sidecar). |
| **Lifespan** | FastAPI's startup/shutdown hook — runs once when the server starts/stops. |
| **Dependency Injection** | FastAPI pattern where route handlers declare what services they need via `Depends()`. |
| **ORM** | Object-Relational Mapping — interact with DB using Python classes instead of raw SQL. |
| **Alembic** | Database migration tool — version controls your schema changes. |
| **uv** | Ultra-fast Python package manager (replaces pip + venv). |

---

*Last updated: February 2026*
