"""
Rename Service — AI-generated file name suggestions.

Asks the local LLM (via llama-cpp-python, in-process) to propose
descriptive filenames based on the file extension and AI-generated summary.
"""

import logging
from pathlib import Path
import re

from app.core.ai_exceptions import AiCapabilityUnavailableError
from app.core.config import settings
from app.observability.tracing import observe, update_current_generation
from app.services.ai.llm_client import llm_client

logger = logging.getLogger(__name__)


class RenameService:
    """Generate intelligent rename suggestions using the local LLM."""

    @staticmethod
    def _normalize_candidate(value: str) -> str:
        value = re.sub(r"\.[A-Za-z0-9]+$", "", value)
        value = value.replace("-", "_")
        value = re.sub(r"[^\w]", "_", value).strip("_").lower()
        return re.sub(r"_+", "_", value)

    @staticmethod
    def _tokens_overlap(candidate_token: str, original_token: str) -> bool:
        if candidate_token == original_token:
            return True
        if min(len(candidate_token), len(original_token)) < 4:
            return False
        return candidate_token.startswith(original_token) or original_token.startswith(candidate_token)

    def _strip_original_prefix(self, clean: str, original_tokens: list[str]) -> str:
        tokens = [token for token in clean.split("_") if token and not token.isdigit()]
        stripped_prefix = False

        while tokens:
            token = tokens[0]
            if any(self._tokens_overlap(token, original_token) for original_token in original_tokens):
                tokens.pop(0)
                stripped_prefix = True
                continue
            if stripped_prefix and len(token) <= 2:
                tokens.pop(0)
                continue
            break

        return "_".join(tokens)

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

        original_stem = self._normalize_candidate(Path(original_name).stem)
        original_tokens = [token for token in original_stem.split("_") if token and not token.isdigit()]

        prompt = (
            "You are a file naming assistant. Generate exactly "
            f"{count} descriptive filenames for a document. "
            "Infer the document type and subject only from the summary. "
            "Never use person names, company names, dates, IDs, or copied phrases from the summary. "
            "Use generic topic words instead. If the document is a resume, CV, portfolio, or personal profile, "
            "prefer resume, cv, or profile in the filename. "
            "Use snake_case, English only, no extension, and keep each filename under 40 characters. "
            f"Reply with ONLY the {count} filenames, one per line, no numbering or bullets.\n\n"
            f"File extension: {extension or '(none)'}\n"
            f"Summary: {summary}\n"
            f"Suggested filenames:"
        )

        try:
            raw = await llm_client.achat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=settings.rename_output_max_tokens,
            )
            names: list[str] = []
            seen: set[str] = set()
            for line in raw.strip().splitlines():
                # Strip numbering like "1.", "1)", "- " etc.
                line = re.sub(r"^[\d\.\)\-\*\s]+", "", line).strip()
                if not line:
                    continue
                clean = self._normalize_candidate(line)
                clean = self._strip_original_prefix(clean, original_tokens)
                if not clean or clean == original_stem or clean in seen:
                    continue
                if clean:
                    seen.add(clean)
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
