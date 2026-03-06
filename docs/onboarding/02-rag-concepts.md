# 🧠 Understanding RAG-Anything & LightRAG (Beginner Friendly)

> This is the core AI part. Don't worry, we'll go step by step.

## What is RAG?

**RAG = Retrieval-Augmented Generation**

Normal LLMs (like ChatGPT) only know what they were trained on. RAG adds a step:

1. **Store** your own documents in a searchable format
2. **Retrieve** relevant content when you ask a question
3. **Generate** an answer using the LLM + retrieved content

Think of it like giving the AI a cheat-sheet of YOUR documents before it answers.

---

## What is an Embedding?

An **embedding** is turning text into a list of numbers (a "vector"). For example:

```
"cat" → [0.23, -0.15, 0.87, 0.42, ...]   (768 numbers)
"dog" → [0.21, -0.12, 0.85, 0.40, ...]   (768 numbers)  ← similar to "cat"!
"car" → [0.91, 0.33, -0.45, 0.12, ...]   (768 numbers)  ← very different
```

Similar meanings → similar numbers. We use this to compare files to categories!

The embedding model in this project is `gemma-3-1b-it-Q4_K_M.gguf` (the same model used for chat) which outputs embedding vectors from the model's hidden dimension.

---

## What is Cosine Similarity?

After we have embeddings (lists of numbers), we need to measure "how similar are these two texts?"

**Cosine similarity** measures the angle between two vectors:
- `1.0` = identical direction (same meaning)
- `0.0` = perpendicular (unrelated)
- `-1.0` = opposite (opposite meaning)

In this project, we embed a **file** and a **category description**, then compute cosine similarity to score how well the file matches each category.

---

## What is LightRAG?

[LightRAG](https://github.com/HKUDS/LightRAG) is the engine underneath RAG-Anything. It:

1. **Parses** documents (PDF, Word, images, etc.)
2. **Extracts** entities and relationships using an LLM
3. **Builds a Knowledge Graph** — a network of connected concepts
4. **Stores** everything locally (vectors + graph)
5. **Answers queries** by traversing the graph + retrieving relevant chunks

---

## What is RAG-Anything?

[RAG-Anything](https://github.com/RAG-Anything/RAG-Anything) is a **wrapper** around LightRAG that adds:

- Multi-modal support (text, images, tables, etc.) including a **Visual Content Analyzer**
- Easy configuration via `RAGAnythingConfig`
- Simplified API: just call `process_document_complete(file_path)` to ingest
- `vision_model_func` hook — our `_vision_complete` closure passed at init time routes image/table content to `LlmClient.achat_with_vision()` for captioning and layout analysis

---

## How They Connect in Our Project

```
Our Code (rag_service.py)
    │
    ▼
RAGAnything                    ← High-level wrapper
    ├── llm_model_func             ← _llm_complete   → LlmClient.achat()
    ├── vision_model_func          ← _vision_complete → LlmClient.achat_with_vision()
    └── embedding_func             ← _embed          → LlmClient.aembed()
    │
    ▼
LightRAG                      ← Core RAG engine
    ├── Knowledge Graph         (entities + relationships)
    ├── Vector Storage          (embeddings for search)
    └── Document Parsing        (text + image/table via vision model)
    │
    ▼
llama-cpp-python (in-process)   ← Provides LLM + Vision + Embeddings (no server needed)
    ├── achat()                 → text chat completion
    ├── achat_with_vision()     → multimodal chat (image_url blocks)
    └── aembed()                → embedding vectors

Note: a vision-capable GGUF (e.g. Qwen2.5-VL-3B) is needed for full image analysis.
Text-only models still work — LlmClient auto-detects support and falls back gracefully.
```

---

## Storage

All RAG data is stored locally at `.storage/rag_storage/` (dev mode) or `~/.klin/rag_storage/` (production). Inside you'll find LightRAG's internal files:
- Vector indices
- Knowledge graph data
- Parsed document cache

---

**Next:** [File-by-File Walkthrough →](./03-file-walkthrough.md)
