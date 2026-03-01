"""
RAG Service — RAG-Anything integration for semantic file analysis.

Responsibilities:
  • Initialize RAG-Anything with local storage
  • Ingest files by absolute path (no upload)
  • Semantic search across ingested corpus
  • Generate embeddings for text (used by ClassificationService)
  • Embedding cache support (future)

RAG-Anything handles its own vector DB internally — we do NOT
manage a separate vector store.

LLM backend: llama-cpp-python (in-process GGUF model).
"""

import logging
from pathlib import Path
from typing import Any, Optional

import numpy as np

from app.core.config import settings
from app.services.llm_client import llm_client

logger = logging.getLogger(__name__)


class RagService:
    """
    Wrapper around RAG-Anything.

    Designed as a singleton-style service — initialised once via `setup()`,
    then injected into route handlers through FastAPI's dependency system.
    """

    def __init__(self) -> None:
        self._rag: Any = None
        self._embed_func: Any = None  # raw embedding callable
        self._ready: bool = False

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def setup(self) -> None:
        """
        Lazy-initialise the RAG-Anything engine with llama-cpp-python as LLM backend.

        Connection chain:
          RAGAnything  →  LightRAG  →  llama-cpp-python (in-process)

        The GGUF model must already be loaded via `llm_client.startup()`
        before calling this method.

        Called once at application startup (lifespan event).
        """
        if self._ready:
            return

        try:
            from lightrag.utils import EmbeddingFunc
            from raganything import RAGAnything
            from raganything.config import RAGAnythingConfig

            working_dir = Path(settings.rag_working_dir)
            working_dir.mkdir(parents=True, exist_ok=True)

            config = RAGAnythingConfig(
                working_dir=str(working_dir),
            )

            # Keep a reference to the raw embed callable for embed_texts()
            async def _embed(texts: list[str]) -> np.ndarray:
                vectors = await llm_client.aembed(texts)
                return np.array(vectors, dtype=np.float32)

            self._embed_func = _embed

            # Embedding function configured for llama-cpp-python
            embedding_func = EmbeddingFunc(
                embedding_dim=settings.embedding_dim,
                max_token_size=settings.max_token_size,
                func=_embed,
            )

            # LLM completion function via in-process model
            async def _llm_complete(prompt, system_prompt=None, history_messages=None, **kwargs):
                messages: list[dict[str, str]] = []
                if system_prompt:
                    messages.append({"role": "system", "content": system_prompt})
                if history_messages:
                    messages.extend(history_messages)
                messages.append({"role": "user", "content": prompt})
                return await llm_client.achat(messages)

            self._rag = RAGAnything(
                config=config,
                llm_model_func=_llm_complete,
                embedding_func=embedding_func,
            )
            self._ready = True
            logger.info(
                "RAG-Anything initialised  →  %s  (model: %s, embd_dim: %d)",
                working_dir,
                settings.model_path,
                settings.embedding_dim,
            )
        except Exception as exc:
            logger.error("Failed to initialise RAG-Anything: %s", exc)
            raise

    @property
    def is_ready(self) -> bool:
        return self._ready

    # ── Embedding ────────────────────────────────────────────────────────

    async def embed_texts(self, texts: list[str]) -> Any:
        """
        Generate embeddings for a list of texts.

        Returns a numpy-like array of shape (len(texts), embedding_dim).
        Used by ClassificationService for category ↔ file similarity.
        """
        self._assert_ready()
        return await self._embed_func(texts)

    # ── Ingestion ────────────────────────────────────────────────────────

    async def ingest(self, file_path: str) -> bool:
        """
        Ingest a single file into the RAG engine by its absolute path.

        Returns True on success, False on failure.
        """
        self._assert_ready()

        try:
            path = Path(file_path)
            if not path.exists():
                logger.warning("Ingest skipped — file not found: %s", file_path)
                return False

            # RAG-Anything accepts a file path directly — no upload needed.
            # process_document_complete() parses + inserts into the knowledge graph.
            await self._rag.process_document_complete(
                file_path=str(path.resolve()),
            )
            logger.info("Ingested: %s", file_path)
            return True
        except Exception as exc:
            logger.error("Ingest failed for %s: %s", file_path, exc)
            return False

    # ── Semantic Search ──────────────────────────────────────────────────

    async def semantic_search(
        self,
        query: str,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Search ingested corpus for semantically similar content.

        Returns a list of match dicts with score + metadata.
        """
        self._assert_ready()

        try:
            results = await self._rag.aquery(query)
            return self._format_results(results, top_k)
        except Exception as exc:
            logger.error("Semantic search failed: %s", exc)
            return []

    # ── Duplicate Detection (stub — ready for enhancement) ───────────

    async def find_duplicates(
        self,
        file_path: str,
        threshold: Optional[float] = None,
    ) -> list[dict[str, Any]]:
        """
        Find semantically similar files to the given path.

        Uses `settings.similarity_threshold` unless overridden.
        """
        threshold = threshold or settings.similarity_threshold
        self._assert_ready()

        logger.debug(
            "Duplicate check for %s (threshold=%.2f) — stub",
            file_path,
            threshold,
        )
        return []

    # ── Internals ────────────────────────────────────────────────────────

    def _assert_ready(self) -> None:
        if not self._ready:
            raise RuntimeError(
                "RagService is not initialised. Call setup() first."
            )

    @staticmethod
    def _format_results(raw: Any, top_k: int) -> list[dict[str, Any]]:
        """Normalise RAG-Anything output into a stable dict format."""
        if raw is None:
            return []

        # RAG-Anything may return different shapes — we normalise here
        if isinstance(raw, str):
            return [{"content": raw, "score": 1.0}]

        if isinstance(raw, list):
            return raw[:top_k]

        # Fallback: wrap whatever we got
        return [{"content": str(raw), "score": 1.0}]
