# RAG and AI Concepts (Project-Specific)

This document explains only the concepts you need to work on klin-worker effectively.

## Core Concepts

### Embedding

An embedding is a numeric vector representation of text. In this project, embeddings are generated through llama-server `/embeddings` and used for category scoring.

### Cosine Similarity

Classification compares vectors using cosine similarity.

- `1.0` means very similar semantic direction
- `0.0` means weak relation

API responses convert raw cosine scores to percentages (`score * 100`).

### RAG

RAG combines retrieval and generation:

1. Ingest document content into retrieval storage.
2. Retrieve relevant content.
3. Generate responses/summaries with LLM context.

In klin-worker, RAG is used primarily for ingestion + semantic capability and embedding-backed workflows.

## Current Implementation in klin-worker

### LLM layer

- `app/services/ai/llm_client.py`
- Talks to out-of-process llama-server over HTTP.
- Provides:
  - `achat` (chat completion)
  - `achat_stream` (streaming)
  - `achat_with_vision` (multimodal with fallback)
  - `aembed` (embeddings)

### RAG layer

- `app/services/ai/rag_service.py`
- Wraps RAG-Anything and configures:
  - embedding function (delegates to llama-server)
  - text completion function
  - vision completion function

### Document parsing and ingest

- `app/services/files/docling_parser.py`
- `app/services/background_ingest.py`

Pipeline design:

1. Inline parse with docling
2. Put extracted text into in-memory `TextCache`
3. Enqueue parsed content for background RAG insertion

This keeps API latency lower while still building semantic storage.

## Why This Matters for Developers

1. Summary quality depends on extracted text and AI availability.
2. Category quality depends on embedding quality and category descriptions.
3. Organize latency is heavily affected by cache state and ingest queue behavior.

## Common Failure Modes

1. LLM server unavailable
   - Symptoms: AI capability errors, degraded health.
   - Check `/health` and llama-server process.

2. Embedding endpoint unavailable
   - Symptoms: classification failure or empty categories.
   - Check `llm_client.ensure_embedding_available` path.

3. RAG not ready
   - Symptoms: ingest not queued or semantic features skipped.
   - Check startup logs and `RagService.setup()` outcome.

4. Parse returns empty
   - Symptoms: weak summary fallback (filename-based context).
   - Check docling parser logs and file format compatibility.

## Practical Recommendation

When debugging organize behavior, inspect this order:

1. Scanner output
2. Cache branch (full hit / partial / full run)
3. AI capability check
4. Parse + queue status
5. Summary/rename outputs
6. Classification scores and history metadata

Continue to [03-file-walkthrough.md](./03-file-walkthrough.md) for concrete file map and code navigation.
