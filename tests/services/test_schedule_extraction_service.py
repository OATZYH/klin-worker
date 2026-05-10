from __future__ import annotations

import asyncio
import json

import app.services.organize.schedule_extraction_service as schedule_module
from app.services.ai.llm_client import llm_client
from app.services.organize.schedule_extraction_service import ScheduleExtractionService


def _meeting_payload() -> str:
    return json.dumps({
        "events": [
            {
                "type": "meeting",
                "confidence": 0.86,
                "source_pages": [1],
                "source_text": "Project Sync, 10 May 2026, 14:00-15:00",
                "missing_fields": [],
                "google_event": {
                    "summary": "Project Sync",
                    "description": "Extracted from project-sync-agenda.pdf",
                    "location": "Google Meet",
                    "start": {
                        "dateTime": "2026-05-10T14:00:00+07:00",
                        "timeZone": "Asia/Bangkok",
                    },
                    "end": {
                        "dateTime": "2026-05-10T15:00:00+07:00",
                        "timeZone": "Asia/Bangkok",
                    },
                    "attendees": [
                        {"email": "person@example.com", "displayName": "Person"}
                    ],
                    "reminders": {"useDefault": True},
                },
            }
        ],
        "error": None,
    })


def _flight_payload() -> str:
    event = {
        "type": "flight",
        "confidence": 0.9,
        "source_pages": [2],
        "source_text": "Flight TG123 BKK to NRT departure 08:30",
        "missing_fields": [],
        "google_event": {
            "summary": "Flight TG123 BKK to NRT",
            "description": "Extracted flight itinerary",
            "location": "BKK Airport",
            "start": {
                "dateTime": "2026-06-01T08:30:00+07:00",
                "timeZone": "Asia/Bangkok",
            },
            "end": {
                "dateTime": "2026-06-01T16:10:00+09:00",
                "timeZone": "Asia/Tokyo",
            },
            "attendees": [],
            "reminders": {"useDefault": True},
        },
    }
    second = event | {
        "source_text": "Flight TG124 NRT to BKK departure 18:00",
        "google_event": event["google_event"] | {
            "summary": "Flight TG124 NRT to BKK",
        },
    }
    return json.dumps({"events": [event, second], "error": None})


def _fenced_array_payload() -> str:
    return """```json
[
  {
    "type": "flight",
    "confidence": 0.9,
    "source_pages": 1,
    "source_text": "Flight VZ101 boarding time 07:10",
    "missing_fields": [],
    "google_event": {
      "summary": "Flight VZ101",
      "description": "Boarding pass",
      "location": "CNX",
      "start": {
        "dateTime": "2022-11-22T07:55:00+07:00",
        "timeZone": "Asia/Bangkok"
      },
      "end": {
        "dateTime": "2022-11-22T09:15:00+07:00",
        "timeZone": "Asia/Bangkok"
      },
      "attendees": [],
      "reminders": {"useDefault": true}
    }
  }
]
```"""


def test_extract_meeting_event_from_candidate_page() -> None:
    calls: list[str] = []

    async def fake_achat(messages, **kwargs) -> str:
        calls.append(messages[0]["content"])
        return _meeting_payload()

    async def run() -> None:
        original_achat = llm_client.achat
        llm_client.achat = fake_achat
        try:
            result = await ScheduleExtractionService().extract(
                file_path="/tmp/project-sync-agenda.pdf",
                content_list=[
                    {
                        "type": "text",
                        "text": "Project Sync meeting on 10 May 2026 at 14:00-15:00",
                        "page_idx": 1,
                    }
                ],
            )
        finally:
            llm_client.achat = original_achat

        assert len(result.events) == 1
        event = result.events[0]
        assert event.type == "meeting"
        assert event.google_event.summary == "Project Sync"
        assert event.google_event.start.dateTime == "2026-05-10T14:00:00+07:00"
        assert event.google_event.attendees[0].email == "person@example.com"
        assert calls

    asyncio.run(run())


def test_extract_flight_itinerary_can_return_multiple_events() -> None:
    async def fake_achat(*args, **kwargs) -> str:
        return _flight_payload()

    async def run() -> None:
        original_achat = llm_client.achat
        llm_client.achat = fake_achat
        try:
            result = await ScheduleExtractionService().extract(
                file_path="/tmp/flight.pdf",
                content_list=[
                    {
                        "type": "text",
                        "text": "Flight TG123 BKK to NRT departure 08:30. Flight TG124 NRT to BKK.",
                        "page_idx": 2,
                    }
                ],
            )
        finally:
            llm_client.achat = original_achat

        assert [event.type for event in result.events] == ["flight", "flight"]
        assert result.events[1].google_event.summary == "Flight TG124 NRT to BKK"

    asyncio.run(run())


def test_long_document_sends_only_candidate_pages_to_llm() -> None:
    prompts: list[str] = []

    async def fake_achat(messages, **kwargs) -> str:
        prompts.append(messages[0]["content"])
        return _meeting_payload()

    async def run() -> None:
        content_list = [
            {"type": "text", "text": f"filler page {page}", "page_idx": page}
            for page in range(1, 40)
        ]
        content_list[24] = {
            "type": "text",
            "text": "Quarterly meeting agenda on 2026-05-10 at 14:00",
            "page_idx": 25,
        }

        original_achat = llm_client.achat
        llm_client.achat = fake_achat
        try:
            await ScheduleExtractionService().extract(
                file_path="/tmp/long.pdf",
                content_list=content_list,
            )
        finally:
            llm_client.achat = original_achat

        assert prompts
        assert "[Page 25]" in prompts[0]
        assert "filler page 1" not in prompts[0]
        assert "Candidate pages: 24, 25, 26" in prompts[0]

    asyncio.run(run())


def test_no_schedule_signals_returns_empty_without_llm_call() -> None:
    async def fake_achat(*args, **kwargs) -> str:
        raise AssertionError("LLM should not be called without schedule evidence")

    async def run() -> None:
        original_achat = llm_client.achat
        llm_client.achat = fake_achat
        try:
            result = await ScheduleExtractionService().extract(
                file_path="/tmp/report.pdf",
                content_list=[
                    {
                        "type": "text",
                        "text": "This report contains financial analysis only.",
                        "page_idx": 1,
                    }
                ],
            )
        finally:
            llm_client.achat = original_achat

        assert result.events == []
        assert result.error is None

    asyncio.run(run())


def test_malformed_llm_json_returns_error() -> None:
    async def fake_achat(*args, **kwargs) -> str:
        return "not json"

    async def run() -> None:
        original_achat = llm_client.achat
        llm_client.achat = fake_achat
        try:
            result = await ScheduleExtractionService().extract(
                file_path="/tmp/meeting.pdf",
                content_list=[
                    {
                        "type": "text",
                        "text": "Meeting agenda on 2026-05-10 at 14:00",
                        "page_idx": 1,
                    }
                ],
            )
        finally:
            llm_client.achat = original_achat

        assert result.events == []
        assert result.error == "Schedule extraction returned invalid JSON."

    asyncio.run(run())


def test_fenced_top_level_array_is_repaired_and_source_pages_normalized() -> None:
    async def fake_achat(*args, **kwargs) -> str:
        return _fenced_array_payload()

    async def run() -> None:
        original_achat = llm_client.achat
        llm_client.achat = fake_achat
        try:
            result = await ScheduleExtractionService().extract(
                file_path="/tmp/boarding-pass.pdf",
                content_list=[
                    {
                        "type": "text",
                        "text": "Flight VZ101 from CNX to BKK on 22 NOV 2022 boarding 07:10",
                        "page_idx": 1,
                    }
                ],
            )
        finally:
            llm_client.achat = original_achat

        assert len(result.events) == 1
        assert result.error is None
        assert result.events[0].type == "flight"
        assert result.events[0].source_pages == [1]

    asyncio.run(run())


def test_schedule_extraction_timeout_returns_error_without_raising() -> None:
    async def slow_achat(*args, **kwargs) -> str:
        await asyncio.sleep(0.05)
        return _meeting_payload()

    async def run() -> None:
        original_achat = llm_client.achat
        original_timeout = schedule_module._SCHEDULE_LLM_TIMEOUT_SECONDS
        llm_client.achat = slow_achat
        schedule_module._SCHEDULE_LLM_TIMEOUT_SECONDS = 0.001
        try:
            result = await ScheduleExtractionService().extract(
                file_path="/tmp/meeting.pdf",
                content_list=[
                    {
                        "type": "text",
                        "text": "Meeting agenda on 2026-05-10 at 14:00",
                        "page_idx": 1,
                    }
                ],
            )
        finally:
            llm_client.achat = original_achat
            schedule_module._SCHEDULE_LLM_TIMEOUT_SECONDS = original_timeout

        assert result.events == []
        assert result.error == "Schedule extraction timed out."

    asyncio.run(run())
