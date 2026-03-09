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

LLM backend: llama-server (out-of-process, managed by Tauri).
"""

import logging
from pathlib import Path
import time
from typing import Any, Optional

import numpy as np

from app.core.ai_exceptions import AiCapabilityUnavailableError
from app.core.config import settings
from app.services.llm_client import llm_client

logger = logging.getLogger(__name__)


def _coerce_positive_int(value: Any) -> int | None:
    """Convert a generation arg to a positive int when possible."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _resolve_llm_max_tokens(kwargs: dict[str, Any]) -> int:
    """Pick a sane generation budget for internal RAG llama requests."""
    for key in ("max_tokens", "n_predict", "max_new_tokens"):
        resolved = _coerce_positive_int(kwargs.get(key))
        if resolved is not None:
            return resolved
    return settings.rag_llm_max_tokens


def _resolve_llm_temperature(kwargs: dict[str, Any]) -> float:
    """Propagate temperature when provided by the caller."""
    try:
        return float(kwargs.get("temperature", 0.3))
    except (TypeError, ValueError):
        return 0.3


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
        Lazy-initialise the RAG-Anything engine with llama-server as LLM backend.

        Connection chain:
          RAGAnything  →  LightRAG  →  llama-server (out-of-process via httpx)

        The llm_client must already be initialised via ``llm_client.startup()``
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

            # Embedding function configured for llama-server
            embedding_func = EmbeddingFunc(
                embedding_dim=settings.embedding_dim,
                max_token_size=settings.max_token_size,
                func=_embed,
            )

            # LLM completion function via llama-server
            async def _llm_complete(prompt, system_prompt=None, history_messages=None, **kwargs):
                messages: list[dict[str, str]] = []
                if system_prompt:
                    messages.append({"role": "system", "content": system_prompt})
                if history_messages:
                    messages.extend(history_messages)
                messages.append({"role": "user", "content": prompt})
                return await llm_client.achat(
                    messages,
                    temperature=_resolve_llm_temperature(kwargs),
                    max_tokens=_resolve_llm_max_tokens(kwargs),
                )

            # Vision / multimodal completion for RAG-Anything's
            # Visual Content Analyzer (image captions, table analysis, etc.)
            async def _vision_complete(
                prompt,
                system_prompt=None,
                history_messages=None,
                image_data=None,
                messages=None,
                **kwargs,
            ):
                temperature = _resolve_llm_temperature(kwargs)
                max_tokens = _resolve_llm_max_tokens(kwargs)
                if messages:
                    # Pre-formatted multimodal messages from RAG-Anything
                    return await llm_client.achat_with_vision(
                        messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                elif image_data:
                    # Raw base64 image — build OpenAI-style multimodal message
                    content: list[dict] = [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_data}",
                            },
                        },
                    ]
                    msgs: list[dict] = []
                    if system_prompt:
                        msgs.append({"role": "system", "content": system_prompt})
                    msgs.append({"role": "user", "content": content})
                    return await llm_client.achat_with_vision(
                        msgs,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                else:
                    # No visual content — use standard text LLM
                    return await _llm_complete(
                        prompt, system_prompt, history_messages, **kwargs
                    )

            self._rag = RAGAnything(
                config=config,
                llm_model_func=_llm_complete,
                vision_model_func=_vision_complete,
                embedding_func=embedding_func,
                # Limit concurrency for local in-process models to avoid OOM.
                # Default LightRAG values (8 embed, 4 LLM) are designed for
                # API-based models; local GGUF models share a single process.
                lightrag_kwargs={
                    "embedding_func_max_async": 1,
                    "llm_model_max_async": 1,
                },
            )
            self._ready = True
            logger.info(
                "RAG-Anything initialised  →  %s  (embd_dim: %d)",
                working_dir,
                settings.embedding_dim,
            )
        except Exception as exc:
            logger.error("Failed to initialise RAG-Anything: %s", exc)
            raise

    @property
    def is_ready(self) -> bool:
        return self._ready

    def ensure_ready(self) -> None:
        """Ensure the RAG wrapper is initialised before it is used."""
        if not self._ready:
            raise AiCapabilityUnavailableError(
                "rag",
                "RAG is unavailable because the RAG service is not initialised.",
            )

    async def ensure_embedding_available(self) -> None:
        """Ensure the RAG embedding pipeline is available."""
        self.ensure_ready()
        await llm_client.ensure_embedding_available(require_general_check=False)

    async def ensure_full_pipeline_available(self) -> None:
        """Ensure the full RAG pipeline is available for organize flows."""
        self.ensure_ready()
        await llm_client.ensure_general_available()
        await llm_client.ensure_embedding_available(require_general_check=False)

    # ── Embedding ────────────────────────────────────────────────────────

    async def embed_texts(self, texts: list[str]) -> Any:
        """
        Generate embeddings for a list of texts.

        Returns a numpy-like array of shape (len(texts), embedding_dim).
        Used by ClassificationService for category ↔ file similarity.
        """
        await self.ensure_embedding_available()
        return await self._embed_func(texts)

    # ── Ingestion ────────────────────────────────────────────────────────

    async def ingest(self, file_path: str) -> bool:
        """
        Ingest a single file into the RAG engine by its absolute path.

        Returns True on success, False on failure.
        """
        self.ensure_ready()
        started_at = time.perf_counter()

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
            logger.info(
                "Ingested: %s (%.2f ms)",
                file_path,
                (time.perf_counter() - started_at) * 1000,
            )
            return True
        except Exception as exc:
            logger.error(
                "Ingest failed for %s after %.2f ms: %s",
                file_path,
                (time.perf_counter() - started_at) * 1000,
                exc,
            )
            return False

    # ── Semantic Search ──────────────────────────────────────────────────

    async def semantic_search(
        self,
        query: str,
        top_k: int = 5,
        max_content_chars: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        Search ingested corpus for semantically similar content.

        Returns a list of match dicts with score + metadata.
        """
        self.ensure_ready()

        try:
            results = await self._rag.aquery(query)
            return self._format_results(results, top_k, max_content_chars=max_content_chars)
        except Exception as exc:
            logger.error("Semantic search failed: %s", exc)
            return []

    async def multimodal_search(
        self,
        query: str,
        multimodal_content: list[dict[str, Any]] | None = None,
        top_k: int = 5,
        max_content_chars: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        Search with optional multimodal content for richer results.

        Falls back to standard text query if multimodal is unavailable.
        """
        self.ensure_ready()

        try:
            if multimodal_content and hasattr(self._rag, "aquery_with_multimodal"):
                results = await self._rag.aquery_with_multimodal(
                    query,
                    multimodal_content=multimodal_content,
                    mode="hybrid",
                )
            else:
                results = await self._rag.aquery(query)
            return self._format_results(results, top_k, max_content_chars=max_content_chars)
        except Exception as exc:
            logger.error("Multimodal search failed, falling back to text: %s", exc)
            return await self.semantic_search(
                query,
                top_k,
                max_content_chars=max_content_chars,
            )

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
        self.ensure_ready()

        logger.debug(
            "Duplicate check for %s (threshold=%.2f) — stub",
            file_path,
            threshold,
        )
        return []

    # ── Internals ────────────────────────────────────────────────────────

    def _assert_ready(self) -> None:
        self.ensure_ready()

    @staticmethod
    def _format_results(
        raw: Any,
        top_k: int,
        *,
        max_content_chars: int | None = None,
    ) -> list[dict[str, Any]]:
        """Normalise RAG-Anything output into a stable dict format."""
        if raw is None:
            return []

        def _trim_content(value: Any) -> str:
            text = str(value)
            if max_content_chars and len(text) > max_content_chars:
                return f"{text[:max_content_chars].rstrip()}…"
            return text

        def _normalise_item(item: Any) -> dict[str, Any]:
            if isinstance(item, dict):
                normalised = dict(item)
                if "content" in normalised:
                    normalised["content"] = _trim_content(normalised["content"])
                return normalised
            return {"content": _trim_content(item), "score": 1.0}

        # RAG-Anything may return different shapes — we normalise here
        if isinstance(raw, str):
            return [{"content": _trim_content(raw), "score": 1.0}]

        if isinstance(raw, list):
            return [_normalise_item(item) for item in raw[:top_k]]

        # Fallback: wrap whatever we got
        return [{"content": _trim_content(raw), "score": 1.0}]
