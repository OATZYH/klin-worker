# 🧑‍💻 Klin-Worker — Junior Developer Onboarding Guide

> **Welcome!** This document walks you through the entire Klin-Worker codebase step-by-step.
> By the end, you'll understand every file, every AI process, and what still needs to be done.
>
> The guide is split into small sections — read them in order.

---

## Sections

| # | Section | Description |
|---|---------|-------------|
| 1 | [Overview & Architecture](./onboarding/01-overview.md) | What the project is, tech stack, big-picture diagram |
| 2 | [RAG Concepts](./onboarding/02-rag-concepts.md) | Embeddings, cosine similarity, LightRAG, RAG-Anything |
| 3 | [File-by-File Walkthrough](./onboarding/03-file-walkthrough.md) | Entry points, config, database, request/response models |
| 4 | [Service Layer](./onboarding/04-services.md) | Scanner, RAG, classification, summary, rename, seed, health checks |
| 5 | [API Routes](./onboarding/05-api-routes.md) | Organize, categories CRUD, history endpoints |
| 6 | [Organize Pipeline](./onboarding/06-organize-pipeline.md) | Full step-by-step pipeline + classification example |
| 7 | [TODO / Improvements](./onboarding/07-todos.md) | Outstanding tasks, quick start, glossary |

---

## Quick Start

```bash
# 1. Download GGUF model
mkdir -p models && wget -O models/gemma-3-1b-it-Q4_K_M.gguf \
  https://huggingface.co/google/gemma-3-1b-it-GGUF/resolve/main/gemma-3-1b-it-Q4_K_M.gguf

# 2. Install & run
uv sync
uv run uvicorn app.main:app --reload

# 3. Verify
curl http://127.0.0.1:8000/health
open http://127.0.0.1:8000/docs
```

---

*Start reading → [01 — Overview & Architecture](./onboarding/01-overview.md)*

*Last updated: February 2026*
