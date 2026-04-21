"""
Rename Service — AI-generated file name suggestions.

Asks the local LLM (via llama-cpp-python, in-process) to propose
descriptive filenames based on the file extension and AI-generated summary.
"""

import logging
import re

from app.core.ai_exceptions import AiCapabilityUnavailableError
from app.core.config import settings
from app.observability.tracing import observe, update_current_generation
from app.services.ai.llm_client import llm_client

logger = logging.getLogger(__name__)


class RenameService:
    """Generate intelligent rename suggestions using the local LLM."""

    @observe(name="rename.suggest", capture_input=False, capture_output=False)
    async def suggest_names(
        self,
        original_name: str,
        extension: str,
        summary: str | None,
        count: int = 3,
    ) -> list[str]:
        """
        Ask the LLM for descriptive filenames.

        Returns a list of suggestions (with extension) or an empty list on failure.
        """
        if not summary:
            return []

        prompt = (
            "You are a file naming assistant. Based on the following summary, "
            f"suggest exactly {count} descriptive filenames (no extension). "
            "Use snake_case, keep each under 60 characters, English only. "
            f"Reply with ONLY the {count} filenames, one per line, no numbering or bullets.\n\n"
            f"Original file: {original_name}\n"
            f"Summary: {summary}\n"
            f"Suggested filenames:"
        )

        try:
            raw = await llm_client.achat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.5,
                max_tokens=settings.rename_max_tokens,
            )
            names: list[str] = []
            for line in raw.strip().splitlines():
                # Strip numbering like "1.", "1)", "- " etc.
                line = re.sub(r"^[\d\.\)\-\*\s]+", "", line).strip()
                if not line:
                    continue
                # Sanitise: keep only alphanumerics, underscores, hyphens
                clean = re.sub(r"[^\w\-]", "_", line).strip("_")
                if clean:
                    names.append(f"{clean}{extension}")
            update_current_generation(
                input={"original_name": original_name, "count": count},
                output={"suggestion_count": len(names)},
            )
            return names[:count] if names else []
        except AiCapabilityUnavailableError:
            raise
        except Exception as exc:
            logger.error("Rename suggestion failed: %s", exc)

        return []
