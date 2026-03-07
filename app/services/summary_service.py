"""
Summary Service — AI-generated file summaries.

Asks the local LLM (via llama-cpp-python, in-process) to produce a concise
one-paragraph summary of a file based on its content retrieved from the RAG engine.

Optimisations vs. the original implementation:
  • Images go directly to the vision model — no RAG round-trip for captions.
  • Text files have a direct-read fallback (first N chars from disk) when
    RAG hasn't ingested the file yet (background ingest may still be running).
  • RAG context is only used when it's actually available and non-empty.
"""

import base64
import logging
from pathlib import Path
from typing import Any

import aiofiles

from app.core.config import settings
from app.services.llm_client import llm_client

logger = logging.getLogger(__name__)

# Max chars to read directly from a text file when RAG has no context
_DIRECT_READ_MAX_CHARS = 2000

# Extensions we consider "plain text" for direct-read fallback
_TEXT_EXTENSIONS = {
    ".txt", ".md", ".csv", ".json", ".xml", ".html", ".htm",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".log", ".py",
    ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".cpp", ".h",
    ".rb", ".go", ".rs", ".sh", ".bat", ".ps1", ".sql",
}


class SummaryService:
    """Generate short AI summaries for files."""

    def __init__(self, rag_service: Any) -> None:
        self._rag = rag_service

    _IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff"}

    async def summarise(self, file_path: str) -> str | None:
        """
        Generate a one-paragraph summary for a file.

        Strategy by file type:
          • **Images** — send directly to the vision model (skip RAG).
          • **Text/documents** — try RAG context first, fall back to
            reading the first ~2 000 chars directly from disk.
        """
        p = Path(file_path)

        # ── Image files → direct vision model ────────────────────────
        if p.suffix.lower() in self._IMAGE_EXTENSIONS:
            return await self._summarise_image(p)

        # ── Text / document files → RAG context + disk fallback ──────
        context = await self._get_text_context(p)

        prompt = (
            "You are a file analysis assistant. "
            "Write a concise one-paragraph summary (2-4 sentences) of the following file content. "
            "Focus on what the file is about and its key topics. "
            "Reply with ONLY the summary, no headers or labels.\n\n"
            f"File: {file_path}\n"
            f"Content:\n{context}\n\n"
            "Summary:"
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

    # ── Image summarisation (vision model) ───────────────────────────

    async def _summarise_image(self, p: Path) -> str | None:
        """Send the image directly to the vision model for description."""
        if not llm_client.supports_vision:
            logger.warning(
                "Vision not supported — falling back to filename-only summary for %s",
                p.name,
            )
            return await self._summarise_image_text_fallback(p)

        try:
            image_bytes = p.read_bytes()
            b64 = base64.b64encode(image_bytes).decode("utf-8")

            # Guess MIME type
            suffix = p.suffix.lower()
            mime_map = {
                ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".png": "image/png", ".gif": "image/gif",
                ".bmp": "image/bmp", ".webp": "image/webp",
                ".tiff": "image/tiff",
            }
            mime = mime_map.get(suffix, "image/jpeg")

            messages: list[dict] = [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "You are a file analysis assistant. "
                                "Describe this image in a concise one-paragraph summary (2-4 sentences). "
                                "Focus on the main subject, text content (if any), and visual elements. "
                                "Reply with ONLY the summary, no headers or labels."
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}"},
                        },
                    ],
                }
            ]

            content = await llm_client.achat_with_vision(
                messages,
                temperature=0.3,
                max_tokens=settings.summary_max_tokens,
            )
            return content.strip() or None
        except Exception as exc:
            logger.error("Vision summary failed for %s: %s", p.name, exc)
            return await self._summarise_image_text_fallback(p)

    async def _summarise_image_text_fallback(self, p: Path) -> str | None:
        """Fallback summary for images when vision is unavailable."""
        prompt = (
            "You are a file analysis assistant. "
            "Write a very brief summary for an image file based only on its filename. "
            "Reply with ONLY the summary.\n\n"
            f"Filename: {p.name}\n"
            "Summary:"
        )
        try:
            content = await llm_client.achat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=settings.summary_max_tokens,
            )
            return content.strip() or None
        except Exception as exc:
            logger.error("Image text-fallback summary failed for %s: %s", p.name, exc)
            return None

    # ── Text context retrieval ───────────────────────────────────────

    async def _get_text_context(self, p: Path) -> str:
        """
        Gather text context for summarisation.

        Priority:
          1. RAG semantic search (if RAG is ready and returns content)
          2. Direct file read (first N chars for known text formats)
          3. Filename-only fallback
        """
        # Try RAG first
        context = await self._query_rag_context(p)
        if context:
            return context

        # Direct-read fallback for text files
        context = await self._read_file_head(p)
        if context:
            return context

        # Last resort
        return f"Filename: {p.name}, Extension: {p.suffix}, Size: file on disk"

    async def _query_rag_context(self, p: Path) -> str:
        """Query RAG for content related to the file."""
        if not self._rag.is_ready:
            return ""

        try:
            query = f"Content of file {p.name}"
            results = await self._rag.semantic_search(
                query,
                top_k=settings.summary_rag_top_k,
                max_content_chars=settings.summary_context_max_chars,
            )
            if results:
                return "\n".join(
                    r.get("content", str(r)) if isinstance(r, dict) else str(r)
                    for r in results
                )
        except Exception as exc:
            logger.warning("RAG query for summary context failed: %s", exc)

        return ""

    async def _read_file_head(self, p: Path) -> str:
        """Read the first N chars of a text file directly from disk."""
        if p.suffix.lower() not in _TEXT_EXTENSIONS:
            return ""

        try:
            async with aiofiles.open(str(p), mode="r", encoding="utf-8", errors="replace") as f:
                content = await f.read(_DIRECT_READ_MAX_CHARS)
            if content and content.strip():
                return content.strip()
        except Exception as exc:
            logger.debug("Direct file read failed for %s: %s", p.name, exc)

        return ""
