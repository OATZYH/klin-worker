from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models.response import (
    CategoryResponse,
    CategoryScoreResponse,
    FileSearchResponse,
    FileSearchResultItem,
    GoogleCalendarDateTimeResponse,
    GoogleCalendarEventDraftResponse,
    HistoryListResponse,
    HistoryLogResponse,
    OrganizeFileResult,
    OrganizeResponse,
    ScheduleEventCandidate,
    ScheduleExtractionResponse,
    SelectedCategoryScoreResponse,
)

pytestmark = pytest.mark.unit


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


# ── New coverage ──────────────────────────────────────────────────────────


def test_organize_response_keys_results_by_file_path() -> None:
    inner = OrganizeFileResult(
        file_id="file-1",
        suggested_names=["a.pdf"],
        categories=[],
    )
    response = OrganizeResponse(results={"/tmp/source/a.pdf": inner})
    payload = response.model_dump()
    assert list(payload["results"].keys()) == ["/tmp/source/a.pdf"]
    assert payload["results"]["/tmp/source/a.pdf"]["file_id"] == "file-1"


def test_file_search_response_defaults() -> None:
    response = FileSearchResponse(results=[])
    payload = response.model_dump()
    assert payload["semantic_status"] == "ready"
    assert payload["semantic_error"] is None
    assert payload["indexing_pending_count"] == 0


def test_file_search_result_item_round_trip() -> None:
    now = datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc)
    item = FileSearchResultItem(
        id="f-1",
        file_name="report.pdf",
        file_type="pdf",
        size_bytes=42,
        folder="/tmp",
        last_edited=now,
        path="/tmp/report.pdf",
    )
    dumped = item.model_dump()
    assert dumped["file_name"] == "report.pdf"
    assert dumped["last_edited"] == now


def test_history_list_response_has_more_default_false() -> None:
    response = HistoryListResponse(results=[])
    assert response.has_more is False
    assert response.limit == 0
    assert response.offset == 0


def test_history_log_response_accepts_minimal_payload() -> None:
    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
    log = HistoryLogResponse(
        id="h-1",
        file_id="f-1",
        action="renamed",
        file_name="x.pdf",
        created_at=now,
    )
    assert log.category is None
    assert log.source_files is None
    assert log.original_path is None


def test_category_response_passes_through_fields() -> None:
    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
    cat = CategoryResponse(
        id="c1",
        name="Work",
        description="desc",
        color="#aabbcc",
        icon="Briefcase",
        enabled=True,
        folder_path="/tmp/work",
        learning=True,
        is_auto_description=False,
        updated_at=now,
    )
    assert cat.color == "#aabbcc"
    assert cat.learning is True
    assert cat.icon == "Briefcase"


def test_selected_category_score_allows_null_score() -> None:
    selected = SelectedCategoryScoreResponse(id="c1", name="Work")
    assert selected.score is None


def test_category_score_response_keeps_float_score() -> None:
    score = CategoryScoreResponse(category_id="c1", name="Work", score=87.4)
    assert score.score == 87.4
