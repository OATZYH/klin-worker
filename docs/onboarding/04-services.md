# ⚙️ Service Layer (The Brain)

All business logic lives in `app/services/`. Each service is a small, focused class.

---

## `scanner_service.py` — File Metadata Extraction

**What it does:** Takes a file path → returns metadata (or an error).

```python
async def scan(self, file_path: str) -> FileScanResult:
    # 1. Security check — reject /System, /Library, etc.
    # 2. Check file exists and is readable
    # 3. Extract metadata: size, extension
    # 4. Compute SHA-256 hash (async, chunked)
    # Returns: FileScanResult(original_path, file_name, extension, size_bytes, sha256)
```

**Security model:**
- `blocked_directories`: Hard-coded system paths you can never scan
- `allowed_roots`: If set, you can ONLY scan inside these directories
- Uses `path.resolve()` to prevent symlink attacks

**Async hashing:** Uses `aiofiles` to read the file in 8KB chunks without blocking the event loop:
```python
async with aiofiles.open(path, "rb") as f:
    while chunk := await f.read(8192):
        sha.update(chunk)
```

---

## `rag_service.py` — RAG-Anything Wrapper

**The most important AI service.** Manages the connection to RAG-Anything + llama-cpp-python.

**`setup()` — Initialization (called once at startup):**
```python
async def setup(self):
    # 1. Import RAG-Anything + LightRAG
    # 2. Create working directory (.storage/rag_storage/)
    # 3. Configure embedding function → uses llm_client.aembed()
    # 4. Configure LLM text function → uses llm_client.achat()
    # 5. Configure vision function (_vision_complete) → routes image/table content:
    #      a. Pre-formatted multimodal messages from RAG-Anything
    #         → llm_client.achat_with_vision(messages)
    #      b. Raw base64 image data
    #         → build OpenAI-style image_url block → llm_client.achat_with_vision()
    #      c. No visual content → fall back to standard _llm_complete()
    # 6. Create RAGAnything instance with:
    #    - llm_model_func    → _llm_complete     (text LLM)
    #    - vision_model_func → _vision_complete  (vision / multimodal LLM)
    #    - embedding_func    → _embed             (in-process)
    #    - lightrag_kwargs   → concurrency limits (max_async=1 for local GGUF)
    # 7. Mark as ready
```

**Connection chain:**
```
RagService.setup()
    → RAGAnything(config, llm_model_func, vision_model_func, embedding_func, lightrag_kwargs)
        → LightRAG internally
            → LlmClient.achat()             (text: entity extraction, graph queries)
            → LlmClient.achat_with_vision() (vision: image captions, table analysis)
            → LlmClient.aembed()            (embeddings: vectors for similarity search)
```

**`embed_texts(texts)` — Generate Embeddings:**
```python
async def embed_texts(self, texts: list[str]) -> numpy_array:
    return await self._embed_func(texts)
```

Used by `ClassificationService` to compare files against categories.

**`ingest(file_path)` — Feed a File to the Knowledge Graph:**
```python
async def ingest(self, file_path: str) -> bool:
    await self._rag.process_document_complete(file_path=str(path.resolve()))
    # RAG-Anything parses the file, extracts entities, builds knowledge graph
```

**`semantic_search(query)` — Search the Knowledge Graph:**
```python
async def semantic_search(self, query: str, top_k: int = 5):
    results = await self._rag.aquery(query)
    # Returns relevant content from all ingested files
```

Used by `SummaryService` to get context about a file before asking the LLM for a summary.

**`multimodal_search(query, multimodal_content)` — Multimodal Search:**
```python
async def multimodal_search(self, query: str, multimodal_content: list | None = None, top_k: int = 5):
    if multimodal_content and hasattr(self._rag, "aquery_with_multimodal"):
        results = await self._rag.aquery_with_multimodal(query, multimodal_content=multimodal_content, mode="hybrid")
    else:
        results = await self._rag.aquery(query)  # falls back to text-only
```

Accepts optional `multimodal_content` (list of image/text dicts) for richer queries. Falls back automatically to `semantic_search` on any error.

---

## `classification_service.py` — AI Category Scoring

**The classification pipeline:**

```python
async def classify(self, file_id, file_path, db, summary=None) -> list[dict]:
    # Step 1: Get file embedding (now uses summary when available!)
    file_embedding = await self._get_file_embedding(file_path, summary=summary)
    # With summary: "report .pdf. This PDF contains a quarterly financial report..."
    # Without:      "report .pdf" (filename-only fallback)

    # Step 2: Load all active categories that have embeddings
    categories = await db.execute(
        select(Category).where(Category.is_active == True, Category.embedding != None)
    )

    # Step 3: For each category, compute cosine similarity
    for cat in categories:
        cat_embedding = json.loads(cat.embedding)  # Stored as JSON string
        score = cosine_similarity(file_embedding, cat_embedding)

    # Step 4: Sort by score (highest first), keep top K
    # Step 5: Save scores to category_scores table
```

**How category embeddings are created:**
```python
# In seed_service.py — used by both seed and CRUD:
def _build_embed_text(cat):
    return "\n".join([cat.name, cat.description])

# In settings/categories.py router:
embed_text = _build_embed_text(cat)
embedding_vec = await classifier.generate_category_embedding(embed_text)
cat.embedding = json.dumps(embedding_vec)  # Stored as JSON string in DB
```

**Score upsert:** Old scores for a file are deleted before inserting new ones (prevents duplicate rows on re-organize).

---

## `summary_service.py` — AI Summaries

```python
async def summarise(self, file_path: str) -> str | None:
    # Step 1: Try to get content from RAG (if available)
    results = await self._rag.semantic_search(f"Content of file {filename}", top_k=3)
    context = "\n".join(results)

    # Step 2: If no RAG content, use filename as fallback context

    # Step 3: Ask LLM for a summary via llama-cpp-python (in-process)
    prompt = "Write a concise one-paragraph summary (2-4 sentences)..."
    response = await llm_client.achat(messages=[{"role": "user", "content": prompt}])
    return response.strip()
```

**Note:** Uses `LlmClient.achat()` which delegates to `asyncio.to_thread()` for non-blocking inference.

---

## `rename_service.py` — AI Rename Suggestions

```python
async def suggest_name(self, original_name, extension, summary) -> str | None:
    prompt = "Suggest a single descriptive filename (snake_case, under 60 chars)..."
    raw = await llm_client.achat(messages=[{"role": "user", "content": prompt}])
    clean = re.sub(r"[^\w\-]", "_", raw).strip("_")  # Sanitize output
    return f"{clean}{extension}"  # e.g. "quarterly_financial_report.pdf"
```

**Important:** The LLM sometimes returns junk. The regex sanitization ensures only safe characters make it through.

---

## `seed_service.py` — Default Category Seeding

Seeds default categories and generates their embeddings.  
**Called by `PUT /api/settings/default-base-path`** during first-run setup,
so the Tauri frontend can supply the OS-specific base path first.

```python
# Default categories store both prose and keyword phrases inside `description`
# so embeddings still capture multilingual and file-type hints.

async def seed_default_categories(db) -> int:
    # Idempotent: safe to call on every boot

async def generate_missing_embeddings(db, classifier) -> int:
    # Find categories where embedding IS NULL → embed and store
```

**Why rich `description` matters:** A category named "Finance & Invoices" works best when the description includes both a sentence and hints like `"receipt, billing statement, bank statement, ใบเสร็จ, ใบกำกับภาษี, .pdf, .xlsx, .csv"`. That single field still gives the embedding broader semantic meaning, including Thai language and file type hints.

---

## `startup_checks.py` — Service Health Verification

After all services are initialised, runs diagnostic checks and logs a summary table.

```python
async def run_all_checks(db, rag_service) → list[CheckResult]
    # ┌──────────────────────────────────────────────┐
    # │  🔍  Startup Service Checks                  │
    # ├──────────────────────────────────────────────┤
    # │  ✅ Database (SQLite)     [OK  ]  ...        │
    # │  ✅ llama-cpp-python      [OK  ]  ...        │
    # │  ✅ RAG-Anything          [OK  ]  ...        │
    # └──────────────────────────────────────────────┘
```

Results are stored in `_startup_checks` (module-level) and exposed via the `/health` endpoint. The llama-cpp-python check also gates embedding generation — if the model failed to load, seeded categories skip embedding and retry on next restart.

---

## `history_service.py` — Audit Log

Simple append-only log. Every action (organize, categorize, etc.) creates a `HistoryLog` entry.

```python
async def log(self, db, file_id, action, metadata):
    entry = HistoryLog(file_id=file_id, action=action, metadata_json=json.dumps(metadata))
    db.add(entry)
```

---

**Next:** [API Routes →](./05-api-routes.md)
