"""
Summary Service — AI-generated file summaries.

Asks the local LLM (via llama-server, out-of-process) to produce a concise
one-paragraph summary of a file.

Text context is provided by the ``TextCache``, which is populated by the
docling parser in the ingest worker before this service is called.
Images go directly to the vision model — no text extraction needed.
"""

import base64
import logging
from pathlib import Path
from langfuse import observe, propagate_attributes

from app.core.ai_exceptions import AiCapabilityUnavailableError
from app.core.config import settings
from app.services.ai.llm_client import llm_client
from app.services.files.text_cache import TextCache

logger = logging.getLogger(__name__)

# Minimum cached text length to be considered useful
_MIN_CACHE_CONTEXT_CHARS = 40


class SummaryService:
    """Generate short AI summaries for files."""

    _IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff"}

    @observe
    async def summarise(
        self, file_path: str, text_cache: TextCache | None = None
    ) -> str | None:
        """
        Generate a one-paragraph summary for a file.

        Strategy by file type:
          • **Images** — send directly to the vision model.
          • **Text/documents** — use docling-parsed text from ``text_cache``.
        """
        with propagate_attributes(session_id="chat-session-123"):
            p = Path(file_path)

            # ── Image files → direct vision model ────────────────────────
            if p.suffix.lower() in self._IMAGE_EXTENSIONS:
                return await self._summarise_image(p)

            # ── Text / document files → text_cache context ───────────────
            context = self._get_text_context(p, text_cache)

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
            except AiCapabilityUnavailableError:
                raise
            except Exception as exc:
                logger.error("Summary generation failed for %s: %s", file_path, exc)
                return None

    # ── Image summarisation (vision model) ───────────────────────────

    @observe
    async def _summarise_image(self, p: Path) -> str | None:
        """Send the image directly to the vision model for description."""
        with propagate_attributes(session_id="chat-session-123"):
            if not llm_client.supports_vision:
                logger.warning(
                    "Vision not supported — falling back to filename-only summary for %s",
                    p.name,
                )
                return await self._summarise_image_text_fallback(p)

            try:
                image_bytes = p.read_bytes()
                b64 = base64.b64encode(image_bytes).decode("utf-8")

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
            except AiCapabilityUnavailableError:
                raise
            except Exception as exc:
                logger.error("Vision summary failed for %s: %s", p.name, exc)
                return await self._summarise_image_text_fallback(p)

    @observe
    async def _summarise_image_text_fallback(self, p: Path) -> str | None:
        """Fallback summary for images when vision is unavailable."""
        prompt = (
            "You are a file analysis assistant. "
            "Write a very brief summary for an image file based only on its filename. "
            "Reply with ONLY the summary.\n\n"
            f"Filename: {p.name}\n"
            "Summary:"
        )
        with propagate_attributes(session_id="chat-session-123"):
            try:
                content = await llm_client.achat(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    max_tokens=settings.summary_max_tokens,
                )
                return content.strip() or None
            except AiCapabilityUnavailableError:
                raise
            except Exception as exc:
                logger.error("Image text-fallback summary failed for %s: %s", p.name, exc)
                return None

    # ── Text context retrieval ───────────────────────────────────────

    @staticmethod
    def _get_text_context(p: Path, text_cache: TextCache | None) -> str:
        """Retrieve text context from the docling-populated cache.

        Priority:
          1. Docling-parsed text from ``text_cache`` (covers all formats)
          2. Filename-only fallback (parser failed or file type unsupported)
        """
        if text_cache is not None:
            cached = text_cache.get(str(p))
            if cached and len(cached) >= _MIN_CACHE_CONTEXT_CHARS:
                text_cache.delete(str(p))  # free memory after consumption
                return cached[: settings.summary_context_max_chars]

        logger.warning("Text cache miss for %s — falling back to filename", p.name)
        return f"Filename: {p.name}, Extension: {p.suffix}"
