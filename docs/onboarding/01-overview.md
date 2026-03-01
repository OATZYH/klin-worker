# 🧑‍💻 What is This Project?

Klin-Worker is a **local AI-powered file organizer**. Think of it like a smart assistant that:

1. You give it a list of file paths (e.g. `/Users/you/Downloads/report.pdf`)
2. It **reads** the file (never moves or deletes it!)
3. It uses **local AI** (llama-cpp-python, in-process GGUF model) to understand what the file is about
4. It **classifies** the file into categories you define (e.g. "Work", "School", "Receipts")
5. It **suggests** a better filename
6. It **returns** all this info to a Tauri desktop app (frontend)

**Key principle: Everything is local. No cloud. No file uploads. Privacy-first.**

---

## Tech Stack Quick Reference

| What | Technology | Why |
|---|---|---|
| API framework | FastAPI | Fast, async, auto-docs at `/docs` |
| Package manager | `uv` | Blazing fast Python package manager (replaces pip) |
| Local LLM | llama-cpp-python + `gemma-3-1b-it-Q4_K_M.gguf` | Runs AI models in-process |
| Embeddings | llama-cpp-python (same model) | Converts text → numbers for comparison |
| Semantic engine | RAG-Anything (wraps LightRAG) | Ingests files, builds a knowledge graph |
| Database | SQLite (async via aiosqlite) | Simple, file-based, no server needed |
| ORM | SQLModel | Pydantic + SQLAlchemy combined |
| Migrations | Alembic | Database schema version control |
| Frontend | Tauri (separate repo) | Desktop app that calls this backend |

---

## How the Pieces Fit Together (Big Picture)

```
┌──────────────────────┐
│   Tauri Frontend     │   ← The desktop app (separate repo)
│   (sends file paths) │
└──────────┬───────────┘
           │  HTTP POST to localhost:8000
           ▼
┌──────────────────────┐
│   FastAPI Server     │   ← THIS PROJECT (klin-worker)
│   app/main.py        │
└──────────┬───────────┘
           │
     ┌─────┴─────┬──────────────┬────────────────┐
     ▼           ▼              ▼                ▼
  Local       SQLite      RAG-Anything     llama-cpp-python
  Files       Database     (LightRAG)       (in-process GGUF)
  (read-only) (klin.db)   (knowledge graph) (no external server)
```

### Data Flow Summary

```
File paths in → Scan metadata → Ingest to RAG → AI Summary → AI Rename → AI Classify → Results out
```

---

**Next:** [Understanding RAG & LightRAG →](./02-rag-concepts.md)
