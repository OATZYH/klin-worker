# 📁 File-by-File Walkthrough

## 1. Entry Points

### `main.py` (Project Root)

```python
# Convenience launcher — same as `uv run fastapi dev app/main.py`
uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=True)
```

**What it does:** Just a shortcut to start the server. You can run `uv run python main.py` or `uv run fastapi dev app/main.py` — same thing.

### `app/main.py` (FastAPI App Factory)

This is the **heart** of the application. It does:

1. **Configures logging** based on `settings.debug`
2. **Creates a singleton `RagService`** instance (one for the whole app)
3. **Defines the `lifespan` function** — runs at startup/shutdown:
   ```python
   @asynccontextmanager
   async def lifespan(app: FastAPI):
       # ON STARTUP:
       Path(settings.database_path).parent.mkdir(...)  # Ensure .storage/ exists
       await run_migrations()                           # Create/update DB tables
       await _rag_service.setup()                       # Initialize RAG-Anything + llama-cpp-python
       _startup_checks = await run_all_checks(db, rag)  # ← Check DB, llama-cpp-python, RAG
       # NOTE: Category seeding is NOT done here.
       # Tauri calls PUT /api/settings/initial-base-path after startup,
       # which sets the OS-specific base path AND seeds default categories.
       yield                                            # ← App runs here
       # ON SHUTDOWN: (cleanup would go here)
   ```
4. **Creates the FastAPI app** with CORS middleware (allows Tauri frontend to connect)
5. **Registers routers**: organize, settings (categories + base path), history
6. **Health check endpoint** at `/health` — returns service status for each checked system:
   ```json
   {
     "status": "ok",
     "version": "0.2.0",
     "services": {
       "Database (SQLite)": { "ok": true, "detail": ".storage/klin.db — connected, has data" },
       "llama-cpp-python":    { "ok": true, "detail": "Model loaded — models/Qwen2.5-VL-3B-Instruct-IQ4_XS.gguf" },
       "RAG-Anything":       { "ok": true, "detail": "Ready — storage: .storage/rag_storage" }
     },
     "rag_ready": true
   }
   ```

   **Vision support:** If the loaded GGUF is a vision-capable model (e.g. Qwen2.5-VL, Qwen3VL), RAG-Anything's Visual Content Analyzer will use it to caption images and analyse tables embedded in documents (PDF, DOCX, etc.). `LlmClient` probes vision support lazily on the first call — text-only models fall back silently without breaking ingestion.

**Key concept — Lifespan:** FastAPI's lifespan is like `__init__` and `__del__` for the whole app. Everything before `yield` runs at startup, everything after runs at shutdown.

---

## 2. Core Configuration

### `app/core/config.py`

A single `Settings` class using `pydantic-settings`. All environment variables are prefixed with `KLIN_`.

**Key settings & what they control:**

| Setting | Default | Used By |
|---|---|---|
| `database_path` | `.storage/klin.db` | SQLite location |
| `rag_working_dir` | `.storage/rag_storage` | RAG-Anything data |
| `llamacpp_model_path` | `models/gemma-3-1b-it-Q4_K_M.gguf` | Path to GGUF model (swap for vision-capable GGUF to enable image analysis) |
| `llamacpp_n_ctx` | `2048` | Context window size |
| `llamacpp_n_gpu_layers` | `0` | GPU offload (-1 = all) |
| `llamacpp_embedding_dim` | `2048` | Vector size (must match model!) |
| `similarity_threshold` | `0.85` | Duplicate detection cutoff |
| `blocked_directories` | `/System`, `/Library`, etc. | Security — can't scan system dirs |

**Smart storage path resolution:**
```python
def _resolve_default_storage_dir() -> Path:
    if getattr(sys, "frozen", False):  # PyInstaller bundle (production)
        return Path(os.environ.get("KLIN_APP_DATA_DIR")) or Path(sys.executable).parent / "data"
    return Path(__file__).resolve().parent.parent.parent / ".storage"  # Dev mode
```

This means:
- **Dev mode** → `.storage/` in project root
- **Production (Tauri sidecar)** → Tauri sets `KLIN_APP_DATA_DIR` to the system app data folder

**Singleton pattern:** `settings = Settings()` at the bottom — import this everywhere.

---

## 3. Database Layer

### `app/db/models.py` — ORM Models (5 Tables)

Uses **SQLModel** (combines Pydantic validation + SQLAlchemy ORM):

| Table | Purpose | Key Columns |
|---|---|---|
| `categories` | Classification buckets (default + user-created) | `name`, `description`, `color`, `embedding` (JSON string), `is_default` |
| `files` | Scanned file metadata | `original_path`, `hash` (SHA-256), `size`, `extension` |
| `file_analysis` | AI-generated analysis per file | `file_id` (FK), `summary`, `suggested_name` |
| `category_scores` | AI classification score per file×category | `file_id` (FK), `category_id` (FK), `score` (float 0-1) |
| `history_logs` | Audit trail of every action | `file_id` (FK), `action`, `metadata_json` |

**Relationships:** `File` → has one `FileAnalysis`, many `CategoryScores`, many `HistoryLogs`.

**Note:** `Category.embedding` stores the embedding vector as a **JSON-serialized string** (e.g. `"[0.23, -0.15, ...]"` ). The vector is generated from `name + description`, and `description` may already contain keyword-style phrases for broader semantic coverage. `is_default=True` marks system-seeded categories.

### `app/db/session.py` — Async Engine & Dependency

```python
engine = create_async_engine(settings.database_url, ...)  # Singleton engine

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSession(engine) as session:
        yield session          # Route handler uses the session
        await session.commit() # Auto-commit on success
        # Auto-rollback on exception
```

**Key concept — Dependency Injection:** FastAPI routes declare `db: AsyncSession = Depends(get_db)` and automatically get a database session. The session is committed when the request finishes, or rolled back if there's an error.

### `app/db/migrations.py` — Alembic at Startup

```python
async def run_migrations() -> None:
    cfg = _get_alembic_config()
    command.upgrade(cfg, "head")  # Apply all pending migrations
```

Called during `lifespan` startup — automatically brings the DB schema up to date.

### `alembic/versions/6e4d8a525088_initial_schema.py`

The first (and currently only) migration. Creates all 5 tables. Generated by Alembic's autogenerate feature based on the SQLModel classes.

---

## 4. Request & Response Models

### `app/models/request.py`

```python
class OrganizeRequest(BaseModel):
    filepaths: list[str]  # Absolute paths like ["/Users/you/Downloads/doc.pdf"]

class CategoryCreate(BaseModel):
    name: str             # e.g. "Receipts"
    description: str      # e.g. "Purchase receipts and invoices\nreceipt, billing, VAT, payment"
    color: str            # Hex color like "#6366f1"
    destination_path: str | None  # Where to move files (optional)

class CategoryUpdate(BaseModel):
    # All fields optional (partial update / PATCH semantics)
    # Changing name or description triggers re-embedding
```

### `app/models/response.py`

Key response types:
- `FileScanResult` — metadata from scanning (path, name, size, hash, error)
- `CategoryResponse` — category info returned to frontend
- `FileAnalysisResponse` — AI summary + suggested name
- `CategoryScoreResponse` — one category×file similarity score
- `TopCategoryResponse` — the best-match category (includes `destination_path`)
- `OrganizeFileResult` — everything combined for one file
- `OrganizeResponse` — list of `OrganizeFileResult` (the main API response)
- `HistoryLogResponse` — audit log entry

---

**Next:** [Service Layer →](./04-services.md)
