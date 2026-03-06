"""
Classification Service — AI-powered file→category scoring.

Uses the RAG embedding engine to compute cosine similarity between
a file's content embedding and each category's embedding vector.
"""

import json
import logging
from typing import Any

import numpy as np
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.db.models import Category, CategoryScore, File

logger = logging.getLogger(__name__)


class ClassificationService:
    """Score files against user-defined categories using embedding similarity."""

    def __init__(self, rag_service: Any) -> None:
        self._rag = rag_service

    # ── Public API ───────────────────────────────────────────────────────

    async def classify(
        self,
        file_id: str,
        file_path: str,
        db: AsyncSession,
        summary: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Score a file against all active categories.

        Args:
            file_id:   DB id of the file record.
            file_path: Absolute path on disk.
            db:        Async DB session.
            summary:   Optional AI-generated summary — when provided, it is
                       combined with the filename for a richer file embedding.

        Returns a sorted list of {"category_id", "name", "score"} dicts.
        """
        # 1. Get file embedding
        file_embedding = await self._get_file_embedding(file_path, summary=summary)
        if file_embedding is None:
            logger.warning("No embedding for %s — skipping classification", file_path)
            return []

        # 2. Load active categories with embeddings
        is_active_column = getattr(Category, "is_active")
        embedding_column = getattr(Category, "embedding")
        result = await db.execute(
            select(Category).where(
                is_active_column.is_(True),
                embedding_column.isnot(None),
            )
        )
        categories = result.scalars().all()

        if not categories:
            logger.info("No categories with embeddings — skipping classification")
            return []

        # 3. Compute cosine similarity for each category
        scores: list[dict[str, Any]] = []
        for cat in categories:
            cat_embedding = json.loads(cat.embedding)
            score = self._cosine_similarity(file_embedding, cat_embedding)
            scores.append({
                "category_id": cat.id,
                "name": cat.name,
                "destination_path": cat.destination_path,
                "score": round(float(score), 4),
            })

        # 4. Sort descending and trim
        scores.sort(key=lambda s: s["score"], reverse=True)
        scores = scores[: settings.classification_top_k]

        # 5. Persist scores to DB
        await self._save_scores(file_id, scores, db)

        return scores

    # ── Embedding helpers ────────────────────────────────────────────────

    async def generate_category_embedding(
        self,
        text: str,
    ) -> list[float] | None:
        """
        Generate an embedding vector for a category description.

        Delegates to the RAG service's underlying embedding function.
        """
        if not self._rag.is_ready:
            logger.warning("RAG not ready — cannot generate category embedding")
            return None

        try:
            vectors = await self._rag.embed_texts([text])
            if vectors is not None and len(vectors) > 0:
                return vectors[0].tolist() if hasattr(vectors[0], "tolist") else list(vectors[0])
        except Exception as exc:
            logger.error("Category embedding failed: %s", exc)
        return None

    async def _get_file_embedding(
        self,
        file_path: str,
        summary: str | None = None,
    ) -> list[float] | None:
        """
        Get the embedding vector for a file.

        When an AI summary is available we embed
        ``"<filename> <extension>. <summary>"`` which captures the actual
        content semantics rather than just the filename.
        """
        if not self._rag.is_ready:
            return None

        try:
            from pathlib import Path

            p = Path(file_path)
            if summary:
                query_text = f"{p.stem} {p.suffix}. {summary}"
            else:
                # Fallback: filename only (lightweight proxy)
                query_text = f"{p.stem} {p.suffix}"

            vectors = await self._rag.embed_texts([query_text])
            if vectors is not None and len(vectors) > 0:
                return vectors[0].tolist() if hasattr(vectors[0], "tolist") else list(vectors[0])
        except Exception as exc:
            logger.error("File embedding failed for %s: %s", file_path, exc)
        return None

    # ── Math ─────────────────────────────────────────────────────────────

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        va = np.array(a, dtype=np.float32)
        vb = np.array(b, dtype=np.float32)
        dot = np.dot(va, vb)
        norm = np.linalg.norm(va) * np.linalg.norm(vb)
        if norm == 0:
            return 0.0
        return float(dot / norm)

    # ── Persistence ──────────────────────────────────────────────────────

    @staticmethod
    async def _save_scores(
        file_id: str,
        scores: list[dict[str, Any]],
        db: AsyncSession,
    ) -> None:
        """Upsert classification scores for a file."""
        # Delete stale scores from previous runs
        from sqlmodel import delete

        file_id_column = getattr(CategoryScore, "file_id")
        await db.execute(
            delete(CategoryScore).where(file_id_column == file_id)
        )

        for s in scores:
            db.add(
                CategoryScore(
                    file_id=file_id,
                    category_id=s["category_id"],
                    score=s["score"],
                )
            )
