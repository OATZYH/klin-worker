"""
Rename Service — AI-generated file name suggestions.

Asks the local LLM (via llama-cpp-python, in-process) to propose a
descriptive filename based on the file extension and AI-generated summary.
"""

import logging
import re

from app.core.config import settings
from app.services.llm_client import llm_client

logger = logging.getLogger(__name__)


class RenameService:
    """Generate intelligent rename suggestions using the local LLM."""

    async def suggest_name(
        self,
        original_name: str,
        extension: str,
        summary: str | None,
    ) -> str | None:
        """
        Ask the LLM for a descriptive filename.

        Returns the suggestion (without extension) or None on failure.
        """
        if not summary:
            return None

        prompt = (
            "You are a file naming assistant. Based on the following summary, "
            "suggest a single descriptive filename (no extension). "
            "Use snake_case, keep it under 60 characters, English only. "
            "Reply with ONLY the filename, nothing else.\n\n"
            f"Original file: {original_name}\n"
            f"Summary: {summary}\n"
            f"Suggested filename:"
        )

        try:
            raw = await llm_client.achat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=settings.rename_max_tokens,
            )
            # Sanitise: keep only alphanumerics, underscores, hyphens
            clean = re.sub(r"[^\w\-]", "_", raw).strip("_")
            if clean:
                return f"{clean}{extension}"
        except Exception as exc:
            logger.error("Rename suggestion failed: %s", exc)

        return None
