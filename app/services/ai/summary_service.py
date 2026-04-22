"""Summary Service — AI-generated file summaries.

Asks the local LLM (via llama-server, out-of-process) to produce a concise
one-paragraph summary of a file.

Text context is provided directly by the fast docling parse executed earlier
in the organize request pipeline. Images go directly to the vision model.
"""

import base64
import logging
from pathlib import Path

from app.core.ai_exceptions import AiCapabilityUnavailableError
from app.core.config import settings
from app.observability.tracing import observe, update_current_generation, update_current_span
from app.services.ai.llm_client import llm_client

logger = logging.getLogger(__name__)

_MIN_EXTRACTED_TEXT_CHARS = 40


class SummaryService:
    """Generate short AI summaries for files."""

    _IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff"}

    @observe(name="summary.generate", capture_input=False, capture_output=False)
    async def summarise(
        self,
        file_path: str,
        extracted_text: str | None = None,
    ) -> str | None:
        """Generate a one-paragraph summary for a file."""
        path = Path(file_path)
        update_current_span(input={"file_path": file_path, "has_extracted_text": bool(extracted_text)})

        if path.suffix.lower() in self._IMAGE_EXTENSIONS:
            return await self._summarise_image(path)

        context = self._get_text_context(path, extracted_text)
        prompt = (
            "You are a file analysis assistant. "
            "Read the following file content carefully and write a clear, informative summary (3-5 sentences). "
            "Cover: what the file is about, key topics or findings, and any important details such as names, dates, or numbers. "
            "Reply with ONLY the summary paragraph, no headers or labels.\n\n"
            f"File: {file_path}\n"
            f"Content:\n{context}\n\n"
            "Summary:"
        )

        try:
            content = await llm_client.achat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=settings.summary_output_max_tokens,
            )
            update_current_span(output={"has_summary": bool(content), "mode": "text"})
            return content.strip() or None
        except AiCapabilityUnavailableError:
            raise
        except Exception as exc:
            logger.error("Summary generation failed for %s: %s", file_path, exc)
            return None

    @observe(name="summary.generate_image", capture_input=False, capture_output=False)
    async def _summarise_image(self, path: Path) -> str | None:
        """Send the image directly to the vision model for description."""
        if not llm_client.supports_vision:
            logger.warning(
                "Vision not supported — falling back to filename-only summary for %s",
                path.name,
            )
            return await self._summarise_image_text_fallback(path)

        try:
            image_bytes = path.read_bytes()
            b64 = base64.b64encode(image_bytes).decode("utf-8")

            suffix = path.suffix.lower()
            mime_map = {
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".png": "image/png",
                ".gif": "image/gif",
                ".bmp": "image/bmp",
                ".webp": "image/webp",
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
                                "You are a visual analysis assistant. "
                                "Examine this image and write a clear summary (3-5 sentences). "
                                "Cover: the main subject or scene, any visible text or data, notable visual elements, and the likely purpose of the image. "
                                "Reply with ONLY the summary paragraph, no headers or labels."
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
                max_tokens=settings.summary_output_max_tokens,
            )
            update_current_generation(output={"has_summary": bool(content), "mode": "vision"})
            return content.strip() or None
        except AiCapabilityUnavailableError:
            raise
        except Exception as exc:
            logger.error("Vision summary failed for %s: %s", path.name, exc)
            return await self._summarise_image_text_fallback(path)

    @observe(name="summary.generate_image_fallback", capture_input=False, capture_output=False)
    async def _summarise_image_text_fallback(self, path: Path) -> str | None:
        """Fallback summary for images when vision is unavailable."""
        prompt = (
            "You are a file analysis assistant. "
            "Based only on the filename below, infer what this image likely contains and write a brief one-sentence description. "
            "Reply with ONLY the description.\n\n"
            f"Filename: {path.name}\n"
            "Description:"
        )
        try:
            content = await llm_client.achat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=settings.summary_output_max_tokens,
            )
            return content.strip() or None
        except AiCapabilityUnavailableError:
            raise
        except Exception as exc:
            logger.error("Image text-fallback summary failed for %s: %s", path.name, exc)
            return None

    @staticmethod
    def _get_text_context(path: Path, extracted_text: str | None) -> str:
        """Resolve summary context from fast parse output or filename fallback."""
        if extracted_text and len(extracted_text) >= _MIN_EXTRACTED_TEXT_CHARS:
            return extracted_text

        logger.warning("No extracted text for %s — falling back to filename", path.name)
        return f"Filename: {path.name}, Extension: {path.suffix}"
