from __future__ import annotations

from app.models.response import (
    CategoryScoreResponse,
    GoogleCalendarDateTimeResponse,
    GoogleCalendarEventDraftResponse,
    OrganizeFileResult,
    ScheduleEventCandidate,
    ScheduleExtractionResponse,
)


def test_organize_result_omits_schedule_when_missing() -> None:
    result = OrganizeFileResult(
        file_id="file-1",
        suggested_names=[],
        categories=[],
    )

    assert "schedule" not in result.model_dump()


def test_organize_result_preserves_google_style_schedule_fields() -> None:
    result = OrganizeFileResult(
        file_id="file-1",
        suggested_names=[],
        categories=[CategoryScoreResponse(category_id="cat-1", name="Meeting", score=91.0)],
        schedule=ScheduleExtractionResponse(
            events=[
                ScheduleEventCandidate(
                    type="meeting",
                    confidence=0.9,
                    source_pages=[1],
                    source_text="Project Sync on 2026-05-10 at 14:00",
                    missing_fields=[],
                    google_event=GoogleCalendarEventDraftResponse(
                        summary="Project Sync",
                        description="Extracted from file",
                        location="Google Meet",
                        start=GoogleCalendarDateTimeResponse(
                            dateTime="2026-05-10T14:00:00+07:00",
                            timeZone="Asia/Bangkok",
                        ),
                        end=GoogleCalendarDateTimeResponse(
                            dateTime="2026-05-10T15:00:00+07:00",
                            timeZone="Asia/Bangkok",
                        ),
                    ),
                )
            ]
        ),
    )

    payload = result.model_dump()

    event = payload["schedule"]["events"][0]["google_event"]
    assert event["start"]["dateTime"] == "2026-05-10T14:00:00+07:00"
    assert event["start"]["timeZone"] == "Asia/Bangkok"
    assert event["reminders"]["useDefault"] is True
