"""Best-effort schedule extraction for organize results.

The service reuses the fast Docling content list already prepared for summary
generation. It only returns local Google Calendar-like event drafts; creating
calendar events remains a frontend/Tauri responsibility.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.core.ai_exceptions import AiCapabilityUnavailableError
from app.core.config import settings
from app.models.response import ScheduleExtractionResponse
from app.observability.tracing import (
    observe,
    start_as_current_observation,
    update_current_observation,
    update_current_span,
)
from app.services.ai.llm_client import llm_client

logger = logging.getLogger(__name__)

_DEFAULT_TIMEZONE = "Asia/Bangkok"
_MAX_CONTEXT_CHARS = 16_000
_SMALL_DOCUMENT_CHARS = 8_000
_SOURCE_TEXT_MAX_CHARS = 1_200
_SCHEDULE_LLM_TIMEOUT_SECONDS = 20.0

_SCHEDULE_SIGNAL_RE = re.compile(
    r"("
    r"\b(meeting|agenda|invite|appointment|calendar|schedule|"
    r"flight|boarding|departure|arrival|gate|terminal|itinerary)\b"
    r"|ประชุม|นัดหมาย|กำหนดการ|เที่ยวบิน|ประตูขึ้นเครื่อง"
    r")",
    re.IGNORECASE,
)
_DATE_TIME_RE = re.compile(
    r"("
    r"\b\d{1,2}[:.]\d{2}\s*(?:am|pm)?\b"
    r"|\b(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\b"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\b"
    r"|\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b"
    r"|\b\d{4}-\d{2}-\d{2}\b"
    r"|วันที่|เวลา"
    r")",
    re.IGNORECASE,
)
_FLIGHT_RE = re.compile(r"\b[A-Z]{2,3}\s?\d{2,4}\b")
_AIRPORT_RE = re.compile(r"\b[A-Z]{3}\b")


class ScheduleExtractionService:
    """Extract Google Calendar-like schedule candidates from parsed file text."""

    @observe(name="schedule.extract", capture_input=False, capture_output=False)
    async def extract(
        self,
        *,
        file_path: str,
        content_list: list[dict[str, Any]] | None,
        extracted_text: str | None = None,
    ) -> ScheduleExtractionResponse:
        """Return schedule candidates without mutating backend or calendar state."""
        pages = self._group_text_by_page(content_list or [])
        fallback_text = extracted_text or self._join_pages(pages)
        if not fallback_text.strip():
            return ScheduleExtractionResponse()

        with start_as_current_observation(
            name="schedule.heuristics",
            as_type="span",
            input={
                "file_path": file_path,
                "page_count": len(pages),
                "fallback_chars": len(fallback_text),
            },
        ):
            context, source_pages = self._build_extraction_context(
                pages=pages,
                fallback_text=fallback_text,
            )
            update_current_observation(
                output={
                    "has_context": bool(context),
                    "source_pages": source_pages,
                    "context_chars": len(context),
                }
            )

        if not context:
            update_current_span(output={"event_count": 0, "reason": "no_schedule_evidence"})
            return ScheduleExtractionResponse()

        update_current_span(
            input={
                "file_path": file_path,
                "source_pages": source_pages,
                "context_chars": len(context),
            }
        )

        prompt = self._build_prompt(
            file_path=file_path,
            context=context,
            source_pages=source_pages,
        )

        try:
            raw = await asyncio.wait_for(
                llm_client.achat(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                    max_tokens=min(2048, settings.llm_output_max_tokens),
                    trace_name="schedule.extract.llm",
                    trace_metadata={
                        "feature": "schedule_extraction",
                        "file_name": Path(file_path).name,
                        "source_pages": source_pages,
                        "context_chars": len(context),
                        "schema": "ScheduleExtractionResponse",
                    },
                ),
                timeout=_SCHEDULE_LLM_TIMEOUT_SECONDS,
            )
        except AiCapabilityUnavailableError:
            raise
        except asyncio.TimeoutError:
            message = "Schedule extraction timed out."
            logger.warning("%s file=%s", message, file_path)
            update_current_span(
                output={"event_count": 0, "error": "timeout"},
                level="ERROR",
                status_message=message,
            )
            return ScheduleExtractionResponse(events=[], error=message)
        except Exception as exc:
            logger.warning("Schedule extraction LLM call failed for %s: %s", file_path, exc)
            update_current_span(
                output={"event_count": 0, "error": str(exc)},
                level="ERROR",
                status_message=str(exc),
            )
            return ScheduleExtractionResponse(events=[], error=str(exc))

        try:
            payload = self._parse_json_payload(raw)
            result = ScheduleExtractionResponse.model_validate(payload)
        except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
            logger.warning("Schedule extraction returned invalid JSON for %s: %s", file_path, exc)
            update_current_span(
                output={"event_count": 0, "error": "invalid_json"},
                level="ERROR",
                status_message=str(exc),
            )
            return ScheduleExtractionResponse(
                events=[],
                error="Schedule extraction returned invalid JSON.",
            )

        for event in result.events:
            if len(event.source_text) > _SOURCE_TEXT_MAX_CHARS:
                event.source_text = event.source_text[:_SOURCE_TEXT_MAX_CHARS].rstrip()
        update_current_span(
            output={
                "event_count": len(result.events),
                "has_error": bool(result.error),
                "event_types": [event.type for event in result.events],
            }
        )
        return result

    @staticmethod
    def _group_text_by_page(content_list: list[dict[str, Any]]) -> dict[int, str]:
        pages: dict[int, list[str]] = {}
        for item in content_list:
            text = ""
            if item.get("type") == "text":
                text = str(item.get("text") or "").strip()
            elif item.get("type") == "table":
                text = str(item.get("table_body") or "").strip()
            if not text:
                continue

            page_idx = item.get("page_idx", 0)
            try:
                page = int(page_idx)
            except (TypeError, ValueError):
                page = 0
            pages.setdefault(page, []).append(text)

        return {page: "\n\n".join(parts) for page, parts in sorted(pages.items())}

    @staticmethod
    def _join_pages(pages: dict[int, str]) -> str:
        return "\n\n".join(text for _, text in sorted(pages.items()))

    def _build_extraction_context(
        self,
        *,
        pages: dict[int, str],
        fallback_text: str,
    ) -> tuple[str, list[int]]:
        candidate_pages = self._find_candidate_pages(pages)
        if candidate_pages:
            return self._build_page_context(pages, candidate_pages)

        if len(fallback_text) <= _SMALL_DOCUMENT_CHARS and self._has_schedule_evidence(
            fallback_text
        ):
            return fallback_text[: self._context_char_limit()], []

        return "", []

    def _find_candidate_pages(self, pages: dict[int, str]) -> list[int]:
        matched: set[int] = set()
        page_numbers = sorted(pages)
        page_number_set = set(page_numbers)

        for page, text in pages.items():
            if not self._has_schedule_evidence(text):
                continue
            matched.add(page)
            for adjacent in (page - 1, page + 1):
                if adjacent in page_number_set:
                    matched.add(adjacent)

        return sorted(matched)

    @staticmethod
    def _has_schedule_evidence(text: str) -> bool:
        return bool(
            (_SCHEDULE_SIGNAL_RE.search(text) and _DATE_TIME_RE.search(text))
            or _FLIGHT_RE.search(text)
            or (_SCHEDULE_SIGNAL_RE.search(text) and _AIRPORT_RE.search(text))
        )

    def _build_page_context(
        self,
        pages: dict[int, str],
        candidate_pages: list[int],
    ) -> tuple[str, list[int]]:
        limit = self._context_char_limit()
        parts: list[str] = []
        included_pages: list[int] = []
        current_len = 0

        for page in candidate_pages:
            text = pages.get(page, "").strip()
            if not text:
                continue

            block = f"[Page {page}]\n{text}"
            separator_len = 2 if parts else 0
            remaining = limit - current_len - separator_len
            if remaining <= 0:
                break
            if len(block) > remaining:
                block = block[:remaining].rstrip()

            parts.append(block)
            included_pages.append(page)
            current_len += len(block) + separator_len

        return "\n\n".join(parts), included_pages

    @staticmethod
    def _context_char_limit() -> int:
        return max(1_000, min(_MAX_CONTEXT_CHARS, settings.llm_input_max_chars - 4_000))

    @staticmethod
    def _parse_json_payload(raw: str) -> dict[str, Any]:
        text = ScheduleExtractionService._strip_markdown_fence(raw.strip())
        text = ScheduleExtractionService._extract_json_region(text)
        parsed = json.loads(text)
        if isinstance(parsed, list):
            parsed = {"events": parsed, "error": None}
        if not isinstance(parsed, dict):
            raise TypeError("Schedule extraction JSON must be an object or event list.")
        return ScheduleExtractionService._normalise_payload(parsed)

    @staticmethod
    def _strip_markdown_fence(text: str) -> str:
        if not text.startswith("```"):
            return text
        lines = text.splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()

    @staticmethod
    def _extract_json_region(text: str) -> str:
        starts = [idx for idx in (text.find("{"), text.find("[")) if idx != -1]
        if not starts:
            return text
        start = min(starts)
        end = max(text.rfind("}"), text.rfind("]"))
        if end > start:
            return text[start : end + 1]
        return text[start:]

    @staticmethod
    def _normalise_payload(payload: dict[str, Any]) -> dict[str, Any]:
        events = payload.get("events")
        if not isinstance(events, list):
            return payload

        normalised_events: list[Any] = []
        for event in events:
            if not isinstance(event, dict):
                normalised_events.append(event)
                continue
            copied = dict(event)
            source_pages = copied.get("source_pages")
            if isinstance(source_pages, int):
                copied["source_pages"] = [source_pages]
            normalised_events.append(copied)

        return {**payload, "events": normalised_events}

    @staticmethod
    def _build_prompt(
        *,
        file_path: str,
        context: str,
        source_pages: list[int],
    ) -> str:
        file_name = Path(file_path).name
        today = datetime.now().astimezone().date().isoformat()
        page_hint = ", ".join(str(page) for page in source_pages) or "unknown"
        return (
            "You extract calendar events from local files. "
            "Return ONLY one valid JSON object, never a markdown fence and never a top-level array. "
            "The exact top-level shape is: "
            '{"events":[...],"error":null}. '
            "Create zero or more event drafts. Do not invent missing dates or times. "
            "If required start/end time is missing, omit that event. "
            "Use RFC3339 dateTime values. If timezone is not explicit, use "
            f"{_DEFAULT_TIMEZONE}. "
            "Use event types: meeting, flight, appointment, other. "
            "Use type flight for boarding passes, flight tickets, and itineraries. "
            "Each event must have: type, confidence, source_pages, source_text, "
            "missing_fields, google_event. "
            "source_pages must always be an array of page numbers. "
            "Keep source_text short; quote only the source lines needed to justify the event. "
            "google_event must have: summary, description, location, start, end, "
            "attendees, reminders. start/end must have dateTime and timeZone. "
            "attendees must contain objects with email and optional displayName. "
            "reminders must be {\"useDefault\": true}. "
            "Do not create Google Meet conferenceData. If an existing meeting URL is present, "
            "place it in location or description.\n\n"
            f"Today: {today}\n"
            f"File: {file_name}\n"
            f"Candidate pages: {page_hint}\n\n"
            f"Content:\n{context}\n\n"
            "JSON:"
        )
