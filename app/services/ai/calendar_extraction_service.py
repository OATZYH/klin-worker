"""Calendar Extraction Service — surface scheduling info from organized files.

Runs in parallel with the summary call inside the organize pipeline. Asks the
local LLM to return a strict JSON object describing a single scheduled event,
or has_event=false when nothing actionable is present.

Returns local-naive ISO timestamps. The frontend attaches the user's IANA
timezone when calling Google Calendar.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, ValidationError

from app.core.ai_exceptions import AiCapabilityUnavailableError
from app.core.config import settings
from app.services.ai.llm_client import llm_client

logger = logging.getLogger(__name__)

_MIN_EXTRACTED_TEXT_CHARS = 60
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)
_PLAIN_TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".rst", ".log",
    ".csv", ".tsv", ".json", ".yml", ".yaml", ".ini", ".cfg", ".toml",
}
_PLAIN_TEXT_MAX_BYTES = 200_000


def _read_plain_text_file(path: Path) -> str:
    """Best-effort plain text read for files docling can't parse."""
    try:
        size = path.stat().st_size
    except OSError:
        return ""
    if size > _PLAIN_TEXT_MAX_BYTES:
        return ""
    for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except (UnicodeDecodeError, OSError):
            continue
    return ""


class CalendarEventDto(BaseModel):
    """Validated shape returned by the extractor (or None when no event)."""

    title: str
    start_iso: str = ""
    end_iso: str = ""
    all_day: bool = False
    location: str = ""
    attendees: list[str] = Field(default_factory=list)
    description: str = ""
    confidence: float = 0.0


class _RawExtraction(BaseModel):
    """Loose schema used to parse the raw LLM response before promoting to a Dto."""

    has_event: bool = False
    title: str = ""
    start_iso: str = ""
    end_iso: str = ""
    all_day: bool = False
    location: str = ""
    attendees: list[str] = Field(default_factory=list)
    description: str = ""
    confidence: float = 0.0


class CalendarExtractionService:
    """Extract a single calendar event from a file's parsed text via the LLM."""

    _IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff"}

    async def extract(
        self,
        file_path: str,
        extracted_text: str | None,
    ) -> Optional[CalendarEventDto]:
        """Return a validated event Dto, or None if no clear event is present."""
        if not settings.extract_calendar_events:
            logger.info("Calendar extraction disabled by setting | file=%s", file_path)
            return None

        path = Path(file_path)
        suffix = path.suffix.lower()
        if suffix in self._IMAGE_EXTENSIONS:
            logger.info("Calendar extraction skipped (image) | file=%s", file_path)
            return None

        text = extracted_text or ""
        if len(text) < _MIN_EXTRACTED_TEXT_CHARS and suffix in _PLAIN_TEXT_EXTENSIONS:
            fallback = _read_plain_text_file(path)
            if fallback:
                logger.info(
                    "Calendar extraction: read plain text fallback (%d chars) | file=%s",
                    len(fallback),
                    file_path,
                )
                text = fallback

        if len(text) < _MIN_EXTRACTED_TEXT_CHARS:
            logger.info(
                "Calendar extraction skipped (text too short %d chars) | file=%s",
                len(text),
                file_path,
            )
            return None

        prompt = self._build_prompt(path.name, text)

        try:
            content = await llm_client.achat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=settings.calendar_event_max_tokens,
            )
        except AiCapabilityUnavailableError:
            logger.warning("Calendar extraction skipped — AI unavailable for %s", file_path)
            return None
        except Exception as exc:
            logger.warning("Calendar extraction LLM call failed for %s: %s", file_path, exc)
            return None

        logger.info("Calendar extraction LLM raw output | file=%s | content=%r", file_path, content)

        raw = self._parse_json(content)
        if raw is None:
            logger.warning("Calendar extraction JSON parse failed | file=%s | raw=%r", file_path, content)
            return None

        if not raw.has_event:
            logger.info("Calendar extraction: has_event=false | file=%s", file_path)
            return None
        if raw.confidence < settings.calendar_event_min_confidence:
            logger.info(
                "Calendar extraction: confidence %.2f below threshold %.2f | file=%s",
                raw.confidence,
                settings.calendar_event_min_confidence,
                file_path,
            )
            return None
        if not raw.title.strip() or not raw.start_iso.strip():
            logger.info(
                "Calendar extraction: missing title/start_iso | title=%r start=%r | file=%s",
                raw.title,
                raw.start_iso,
                file_path,
            )
            return None

        logger.info(
            "Calendar event detected | file=%s | title=%r start=%s confidence=%.2f",
            file_path,
            raw.title,
            raw.start_iso,
            raw.confidence,
        )

        try:
            return CalendarEventDto(
                title=raw.title.strip(),
                start_iso=raw.start_iso.strip(),
                end_iso=raw.end_iso.strip(),
                all_day=raw.all_day,
                location=raw.location.strip(),
                attendees=[a.strip() for a in raw.attendees if a and a.strip()],
                description=raw.description.strip(),
                confidence=float(raw.confidence),
            )
        except ValidationError as exc:
            logger.warning("Calendar Dto validation failed for %s: %s", file_path, exc)
            return None

    @staticmethod
    def _build_prompt(file_name: str, extracted_text: str) -> str:
        truncated = extracted_text[: settings.calendar_event_context_max_chars]
        return (
            "You analyze documents for actionable calendar events. From the file content, "
            "extract a SINGLE upcoming or scheduled event if one is clearly described.\n"
            "Return ONLY valid JSON matching this shape (no prose, no code fences):\n\n"
            "{\"has_event\": boolean, \"title\": string, \"start_iso\": string, "
            "\"end_iso\": string, \"all_day\": boolean, \"location\": string, "
            "\"attendees\": string[], \"description\": string, \"confidence\": number}\n\n"
            "Rules:\n"
            "- has_event = false if the file contains no clear scheduled event.\n"
            "- Do not invent dates. Only extract dates explicitly written in content.\n"
            "- Use local-naive ISO8601 (e.g. 2026-04-12T14:00:00) — no timezone offset.\n"
            "- If only a date (no time) is present, set all_day = true and use date-only "
            "  start_iso (e.g. 2026-04-12).\n"
            "- end_iso is optional. Leave as empty string if unknown.\n"
            "- attendees is a list of names/emails explicitly mentioned. Empty if none.\n"
            "- confidence is between 0 and 1; below 0.55 → has_event must be false.\n\n"
            f"File: {file_name}\n"
            f"Content:\n{truncated}\n\n"
            "JSON:"
        )

    @staticmethod
    def _parse_json(content: str) -> Optional[_RawExtraction]:
        """Best-effort JSON extraction tolerant of stray code fences or prose."""
        if not content:
            return None

        text = content.strip()
        fenced = _FENCE_RE.search(text)
        if fenced:
            text = fenced.group(1).strip()
        else:
            # Fall back to the first {...} block if the model added prose.
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                text = text[start : end + 1]

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.debug("Calendar JSON parse failed: %s | raw=%r", exc, content[:200])
            return None

        if not isinstance(data, dict):
            return None

        try:
            return _RawExtraction.model_validate(data)
        except ValidationError as exc:
            logger.debug("Calendar JSON schema mismatch: %s", exc)
            return None


calendar_extraction_service = CalendarExtractionService()
