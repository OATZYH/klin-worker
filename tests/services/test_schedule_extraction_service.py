from __future__ import annotations

import asyncio
import json
from datetime import datetime
from zoneinfo import ZoneInfo

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
                "source_text": "Project Sync, 15 Dec 2026, 14:00-15:00",
                "missing_fields": [],
                "google_event": {
                    "summary": "Project Sync",
                    "description": "Extracted from project-sync-agenda.pdf",
                    "location": "Google Meet",
                    "start": {
                        "dateTime": "2026-12-15T14:00:00+07:00",
                        "timeZone": "Asia/Bangkok",
                    },
                    "end": {
                        "dateTime": "2026-12-15T15:00:00+07:00",
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
        "source_text": "Flight TG123 BKK to NRT on 1 Jun 2026 departure 08:30",
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
        "source_text": "Flight TG124 NRT to BKK on 1 Jun 2026 departure 18:00",
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
    "source_text": "Flight VZ101 22 NOV 2030 boarding time 07:10",
    "missing_fields": [],
    "google_event": {
      "summary": "Flight VZ101",
      "description": "Boarding pass",
      "location": "CNX",
      "start": {
        "dateTime": "2030-11-22T07:55:00+07:00",
        "timeZone": "Asia/Bangkok"
      },
      "end": {
        "dateTime": "2030-11-22T09:15:00+07:00",
        "timeZone": "Asia/Bangkok"
      },
      "attendees": [],
      "reminders": {"useDefault": true}
    }
  }
]
```"""


def _reminders_array_payload() -> str:
    payload = json.loads(_meeting_payload())
    payload["events"][0]["google_event"]["reminders"] = [{"useDefault": True}]
    return json.dumps(payload)


def test_extract_meeting_event_from_candidate_page() -> None:
    calls: list[str] = []
    response_format: dict[str, object] = {}

    async def fake_achat(messages, **kwargs) -> str:
        nonlocal response_format
        calls.append(messages[0]["content"])
        response_format = kwargs["response_format"]
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
                        "text": "Project Sync meeting on 15 Dec 2026 at 14:00-15:00",
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
        assert event.google_event.start.dateTime == "2026-12-15T14:00:00+07:00"
        assert event.google_event.attendees[0].email == "person@example.com"
        assert calls
        assert response_format["type"] == "json_schema"

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
                        "text": (
                            "Flight TG123 BKK to NRT on 1 Jun 2026 departure 08:30. "
                            "Flight TG124 NRT to BKK on 1 Jun 2026 departure 18:00."
                        ),
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
            "text": "Quarterly meeting agenda on 2026-12-15 at 14:00",
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
                        "text": "Flight VZ101 from CNX to BKK on 22 NOV 2030 boarding 07:10",
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


def test_reminders_array_is_normalized_to_google_reminders_object() -> None:
    async def fake_achat(*args, **kwargs) -> str:
        return _reminders_array_payload()

    async def run() -> None:
        original_achat = llm_client.achat
        llm_client.achat = fake_achat
        try:
            result = await ScheduleExtractionService().extract(
                file_path="/tmp/meeting.pdf",
                content_list=[
                    {
                        "type": "text",
                        "text": "Meeting agenda on 2026-12-15 at 14:00",
                        "page_idx": 1,
                    }
                ],
            )
        finally:
            llm_client.achat = original_achat

        assert len(result.events) == 1
        assert result.error is None
        assert result.events[0].google_event.reminders.useDefault is True

    asyncio.run(run())


def test_langfuse_shaped_incomplete_json_still_reports_parse_context() -> None:
    raw = (
        '{"events":[{"type":"flight","confidence":100,"source_pages":[1],'
        '"source_text":"Flight No: VZ101","missing_fields":[],'
        '"google_event":{"summary":"Boarding Pass","description":"Passenger details",'
        '"location":"VZ101","start":{"dateTime":"2026-05-10T07:10:00Z",'
        '"timeZone":"Asia/Bangkok"},"end":{"dateTime":"2026-05-10T09:15:00Z",'
        '"timeZone":"Asia/Bangkok"},"attendees":[],"reminders":{"useDefault":true}}]'
    )
    span_updates: list[dict[str, object]] = []

    async def fake_achat(*args, **kwargs) -> str:
        return raw

    def fake_update_current_span(**kwargs) -> None:
        span_updates.append(kwargs)

    async def run() -> None:
        original_achat = llm_client.achat
        original_update_current_span = schedule_module.update_current_span
        llm_client.achat = fake_achat
        schedule_module.update_current_span = fake_update_current_span
        try:
            result = await ScheduleExtractionService().extract(
                file_path="/tmp/boarding-pass.pdf",
                content_list=[
                    {
                        "type": "text",
                        "text": "Flight VZ101 from CNX to BKK on 2026-05-10 boarding 07:10",
                        "page_idx": 1,
                    }
                ],
            )
        finally:
            llm_client.achat = original_achat
            schedule_module.update_current_span = original_update_current_span

        assert result.events == []
        assert result.error == "Schedule extraction returned invalid JSON."
        error_outputs = [
            update["output"]
            for update in span_updates
            if isinstance(update.get("output"), dict)
            and update["output"].get("error") == "invalid_json"
        ]
        assert error_outputs
        parse_error = error_outputs[-1]["parse_error"]
        assert parse_error["type"] == "JSONDecodeError"
        assert "raw_prefix" in parse_error
        assert "raw_suffix" in parse_error

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
        # Schedule extraction now retries once on timeout before giving up,
        # so the surfaced error message reflects the post-retry state.
        assert result.error == "Schedule extraction timed out after retry."

    asyncio.run(run())


def _flight_payload_with_overrides(
    *,
    google_event: dict | None | object = ...,
    start_dt: str | None = None,
    end_dt: str | None = None,
    source_text: str = "Flight TG999 on 15 Jan 2030",
) -> str:
    """Build a single-flight payload with selective google_event overrides."""
    base_event = {
        "type": "flight",
        "confidence": 0.9,
        "source_pages": [1],
        "source_text": source_text,
        "missing_fields": [],
        "google_event": {
            "summary": "Flight TG999",
            "description": "Test flight",
            "location": "BKK",
            "start": {"dateTime": "2030-01-15T08:30:00+07:00", "timeZone": "Asia/Bangkok"},
            "end": {"dateTime": "2030-01-15T10:00:00+07:00", "timeZone": "Asia/Bangkok"},
            "attendees": [],
            "reminders": {"useDefault": True},
        },
    }
    if google_event is not ...:
        base_event["google_event"] = google_event
    else:
        if start_dt is not None:
            base_event["google_event"]["start"]["dateTime"] = start_dt
        if end_dt is not None:
            base_event["google_event"]["end"]["dateTime"] = end_dt
    return json.dumps({"events": [base_event], "error": None})


def _run_extract_with_payload(
    payload: str,
    *,
    content_text: str = "Flight TG999 boarding pass dated 15 Jan 2030 explicitly",
):
    async def fake_achat(*args, **kwargs) -> str:
        return payload

    async def run():
        original_achat = llm_client.achat
        llm_client.achat = fake_achat
        try:
            return await ScheduleExtractionService().extract(
                file_path="/tmp/flight.pdf",
                content_list=[
                    {
                        "type": "text",
                        "text": content_text,
                        "page_idx": 1,
                    }
                ],
            )
        finally:
            llm_client.achat = original_achat

    return asyncio.run(run())


def test_event_with_past_date_is_dropped() -> None:
    payload = _flight_payload_with_overrides(
        start_dt="2022-12-21T10:55:00+07:00",
        end_dt="2022-12-21T12:00:00+07:00",
    )
    result = _run_extract_with_payload(payload)
    assert result.events == []
    assert result.error is None


def test_event_with_no_google_event_is_dropped() -> None:
    payload = _flight_payload_with_overrides(google_event=None)
    result = _run_extract_with_payload(payload)
    assert result.events == []
    assert result.error is None


def test_event_with_blank_start_datetime_is_dropped() -> None:
    payload = _flight_payload_with_overrides(start_dt="")
    result = _run_extract_with_payload(payload)
    assert result.events == []
    assert result.error is None


def test_event_today_or_future_is_kept() -> None:
    payload = _flight_payload_with_overrides(
        start_dt="2099-06-01T08:30:00+07:00",
        end_dt="2099-06-01T10:00:00+07:00",
        source_text="Flight TG999 on 1 Jun 2099",
    )
    result = _run_extract_with_payload(
        payload,
        content_text="Flight TG999 boarding pass dated 1 Jun 2099 explicitly",
    )
    assert len(result.events) == 1
    assert result.events[0].google_event.start.dateTime == "2099-06-01T08:30:00+07:00"


def test_event_with_z_suffix_datetime_is_parsed() -> None:
    payload = _flight_payload_with_overrides(
        start_dt="2099-06-01T01:30:00Z",
        end_dt="2099-06-01T03:00:00Z",
        source_text="Flight TG999 on 1 Jun 2099",
    )
    result = _run_extract_with_payload(
        payload,
        content_text="Flight TG999 boarding pass dated 1 Jun 2099 explicitly",
    )
    assert len(result.events) == 1


def test_event_with_naive_datetime_falls_back_to_bangkok_timezone() -> None:
    # Naive dateTime + missing timeZone should be interpreted in Asia/Bangkok and kept
    # because the date is far in the future.
    payload = _flight_payload_with_overrides(
        google_event={
            "summary": "Flight TG999",
            "description": "Test flight",
            "location": "BKK",
            "start": {"dateTime": "2099-06-01T08:30:00", "timeZone": None},
            "end": {"dateTime": "2099-06-01T10:00:00", "timeZone": None},
            "attendees": [],
            "reminders": {"useDefault": True},
        },
        source_text="Flight TG999 on 1 Jun 2099",
    )
    result = _run_extract_with_payload(
        payload,
        content_text="Flight TG999 boarding pass dated 1 Jun 2099 explicitly",
    )
    assert len(result.events) == 1


def test_complete_source_date_formats_are_kept() -> None:
    cases = [
        "2099-06-01",
        "2099/06/01",
        "1 Jun 2099",
        "June 1 2099",
        "1JUN99",
        "1/06/99",
        "1-06-2099",
        "1 มิถุนายน 2099",
        "1 มิถุนายน 2642",
    ]

    for evidence_date in cases:
        payload = _flight_payload_with_overrides(
            start_dt="2099-06-01T08:30:00+07:00",
            end_dt="2099-06-01T10:00:00+07:00",
            source_text=f"Flight TG999 on {evidence_date}",
        )
        result = _run_extract_with_payload(
            payload,
            content_text=f"Flight TG999 boarding pass dated {evidence_date} at 08:30",
        )

        assert len(result.events) == 1, evidence_date


def test_event_with_today_copied_but_no_complete_source_date_is_dropped() -> None:
    today = datetime.now(ZoneInfo("Asia/Bangkok")).date()
    payload = _flight_payload_with_overrides(
        start_dt=f"{today.isoformat()}T08:30:00+07:00",
        end_dt=f"{today.isoformat()}T10:00:00+07:00",
        source_text=f"Flight TG999 Date: {today.isoformat()}",
    )
    result = _run_extract_with_payload(
        payload,
        content_text="Flight TG999 boarding pass dated 1Jun at 08:30",
    )
    assert result.events == []
    assert result.error is None


def test_event_with_day_month_only_source_date_is_dropped() -> None:
    payload = _flight_payload_with_overrides(
        start_dt="2099-06-01T08:30:00+07:00",
        end_dt="2099-06-01T10:00:00+07:00",
        source_text="Flight TG999 on 1 Jun",
    )
    result = _run_extract_with_payload(
        payload,
        content_text="Flight TG999 boarding pass dated 1 Jun at 08:30",
    )
    assert result.events == []
    assert result.error is None


def test_event_with_month_year_only_source_date_is_dropped() -> None:
    payload = _flight_payload_with_overrides(
        start_dt="2099-06-01T08:30:00+07:00",
        end_dt="2099-06-01T10:00:00+07:00",
        source_text="Flight TG999 in Jun 2099",
    )
    result = _run_extract_with_payload(
        payload,
        content_text="Flight TG999 boarding pass for Jun 2099 at 08:30",
    )
    assert result.events == []
    assert result.error is None


def test_event_with_malformed_datetime_is_dropped() -> None:
    payload = _flight_payload_with_overrides(
        start_dt="not-a-date",
        end_dt="2099-06-01T10:00:00+07:00",
        source_text="Flight TG999 on 1 Jun 2099",
    )
    result = _run_extract_with_payload(
        payload,
        content_text="Flight TG999 boarding pass dated 1 Jun 2099 at 08:30",
    )
    assert result.events == []
    assert result.error is None


def test_event_with_hallucinated_date_mismatch_is_dropped() -> None:
    payload = _flight_payload_with_overrides(
        start_dt="2026-01-11T07:10:00+07:00",
        end_dt="2026-01-11T07:55:00+07:00",
        source_text="Flight No: VZ101, Date: 14 DEC 2026, Time: 07:10",
    )
    result = _run_extract_with_payload(
        payload,
        content_text="Flight No: VZ101, Date: 14 DEC 2026, Time: 07:10, Route: CNX-BKK",
    )
    assert result.events == []
    assert result.error is None
