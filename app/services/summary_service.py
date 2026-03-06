"""
Summary Service — AI-generated file summaries.

Asks the local LLM (via llama-cpp-python, in-process) to produce a concise
one-paragraph summary of a file based on its content retrieved from the RAG engine.
"""

import logging
from typing import Any

from app.core.config import settings
from app.services.llm_client import llm_client

logger = logging.getLogger(__name__)


class SummaryService:
    """Generate short AI summaries for files."""

    def __init__(self, rag_service: Any) -> None:
        self._rag = rag_service

    _IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff"}

    async def summarise(self, file_path: str) -> str | None:
        """
        Generate a one-paragraph summary for a file.

        Steps:
          1. Query RAG for content related to the file
             (uses multimodal search for image files)
          2. Send content to LLM for summarisation
        """
        # 1. Retrieve content from RAG (if available)
        context = ""
        if self._rag.is_ready:
            try:
                from pathlib import Path

                p = Path(file_path)
                query = f"Content of file {p.name}"

                # Use multimodal search for image files
                if p.suffix.lower() in self._IMAGE_EXTENSIONS:
                    results = await self._rag.multimodal_search(
                        query,
                        multimodal_content=[{
                            "type": "image",
                            "file_path": str(p.resolve()),
                        }],
                        top_k=settings.summary_rag_top_k,
                        max_content_chars=settings.summary_context_max_chars,
                    )
                else:
                    results = await self._rag.semantic_search(
                        query,
                        top_k=settings.summary_rag_top_k,
                        max_content_chars=settings.summary_context_max_chars,
                    )

                if results:
                    context = "\n".join(
                        r.get("content", str(r)) if isinstance(r, dict) else str(r)
                        for r in results
                    )
            except Exception as exc:
                logger.warning("RAG query for summary context failed: %s", exc)

        if not context:
            # Fallback: use filename as minimal context
            from pathlib import Path

            p = Path(file_path)
            context = f"Filename: {p.name}, Extension: {p.suffix}, Size: file on disk"

        # 2. Ask LLM via llama-cpp-python (in-process)
        prompt = (
            "You are a file analysis assistant. "
            "Write a concise one-paragraph summary (2-4 sentences) of the following file content. "
            "Focus on what the file is about and its key topics. "
            "Reply with ONLY the summary, no headers or labels.\n\n"
            f"File: {file_path}\n"
            f"Content:\n{context}\n\n"
            f"Summary:"
        )

        try:
            content = await llm_client.achat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=settings.summary_max_tokens,
            )
            return content.strip() or None
        except Exception as exc:
            logger.error("Summary generation failed for %s: %s", file_path, exc)
            return None
