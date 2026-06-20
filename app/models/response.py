"""
Response models for the Klin-Worker API.
"""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ── Category ─────────────────────────────────────────────────────────────


class CategoryResponse(BaseModel):
    """Single category returned to the frontend (V3)."""

    id: str
    name: str
    description: str
    color: str
    icon: str | None = None
    enabled: bool
    folder_path: Optional[str] = None
    learning: bool = False
    is_auto_description: bool = False
    updated_at: datetime


class CategoryScoreResponse(BaseModel):
    """A single category ↔ file score (percentage 0-100)."""

    category_id: str
    name: str
    score: float = Field(description="Confidence percentage (0-100)")


class SelectedCategoryScoreResponse(BaseModel):
    """Category selected by the user for a confirmed action."""

    id: str
    name: str
    score: float | None = Field(default=None, description="Confidence percentage (0-100)")


# ── Schedule Extraction ─────────────────────────────────────────────────

# JSON contract when `OrganizeFileResult.schedule` is present:

# Type shape:
# {
#   "events": [
#     {
#       "type": "meeting" | "flight" | "appointment" | "other",
#       "confidence": number,                 # 0.0 to 1.0
#       "source_pages": number[],
#       "source_text": string,
#       "missing_fields": string[],
#       "google_event": {
#         "summary": string,
#         "description": string | null,
#         "location": string | null,
#         "start": {"dateTime": string, "timeZone": string | null},
#         "end": {"dateTime": string, "timeZone": string | null},
#         "attendees": [{"email": string, "displayName": string | null}],
#         "reminders": {"useDefault": boolean}
#       } | null
#     }
#   ],
#   "error": string | null
# }

# Example:
# {
#   "events": [
#     {
#       "type": "meeting",
#       "confidence": 1.0,
#       "source_pages": [1],
#       "source_text": "Interview Details: Date: Friday, 15 May 2026 Time: 10:30 AM Format: Online Interview via Microsoft Teams Duration: Approximately 1 hour",
#       "missing_fields": [],
#       "google_event": {
#         "summary": "Full Stack Developer Interview with Emily Carter",
#         "description": "Full-stack development concepts problem-solving approach team discussion",
#         "location": "BrightEdge Technology",
#         "start": {
#           "dateTime": "2026-05-15T10:30:00Z",
#           "timeZone": "Asia/Bangkok"
#         },
#         "end": {
#           "dateTime": "2026-05-15T11:30:00Z",
#           "timeZone": "Asia/Bangkok"
#         },
#         "attendees": [],
#         "reminders": {"useDefault": true}
#       }
#     }
#   ],
#   "error": null
# }


class GoogleCalendarDateTimeResponse(BaseModel):
    """Google Calendar event date/time payload prepared for frontend insertion."""

    dateTime: str
    timeZone: str | None = None


class GoogleCalendarAttendeeResponse(BaseModel):
    """Google Calendar attendee payload."""

    email: str
    displayName: str | None = None


class GoogleCalendarRemindersResponse(BaseModel):
    """Google Calendar reminders payload."""

    useDefault: bool = True


class GoogleCalendarEventDraftResponse(BaseModel):
    """Google Calendar-like event body returned as a local draft only."""

    summary: str
    description: str | None = None
    location: str | None = None
    start: GoogleCalendarDateTimeResponse
    end: GoogleCalendarDateTimeResponse
    attendees: list[GoogleCalendarAttendeeResponse] = Field(default_factory=list)
    reminders: GoogleCalendarRemindersResponse = Field(
        default_factory=GoogleCalendarRemindersResponse
    )


class ScheduleEventCandidate(BaseModel):
    """Single schedule candidate extracted from a file."""

    type: Literal["meeting", "flight", "appointment", "other"] = "other"
    confidence: float = Field(ge=0, le=1)
    source_pages: list[int] = Field(default_factory=list)
    source_text: str = ""
    missing_fields: list[str] = Field(default_factory=list)
    google_event: GoogleCalendarEventDraftResponse | None = None


class ScheduleExtractionResponse(BaseModel):
    """Best-effort schedule extraction result for one file."""

    events: list[ScheduleEventCandidate] = Field(default_factory=list)
    error: str | None = None


# ── Organize ─────────────────────────────────────────────────────────────


class OrganizeFileResult(BaseModel):
    """Per-file result returned from POST /api/organize."""

    file_id: str
    suggested_names: list[str] = Field(default_factory=list)
    categories: list[CategoryScoreResponse]
    error: Optional[str] = None
    schedule: ScheduleExtractionResponse | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="Optional Google Calendar-like event drafts extracted from the file.",
    )


class OrganizeResponse(BaseModel):
    """Response for POST /api/organize."""

    results: dict[str, OrganizeFileResult]


class ApplyOrganizeDecisionResponse(BaseModel):
    """Minimal success response for POST /api/organize/apply."""

    success: bool = True


# ── History ──────────────────────────────────────────────────────────────


class HistoryLogResponse(BaseModel):
    """Single audit log entry (V3)."""

    id: str
    file_id: str
    action: str
    file_name: str
    category: SelectedCategoryScoreResponse | None = None
    original_path: Optional[str] = None
    new_path: Optional[str] = None
    source_files: list[str] | None = None
    category_name: str | None = None
    created_at: datetime


class HistoryListResponse(BaseModel):
    """Wrapped history response for V3."""

    results: list[HistoryLogResponse]
    limit: int = 0
    offset: int = 0
    has_more: bool = False


# ── Settings ────────────────────────────────────────────────────────────


class DefaultBasePathResponse(BaseModel):
    """Response for default base path setting."""

    default_base_path: Optional[str] = None
    updated_count: int = 0
    updated_categories: list[CategoryResponse] = []


class AutoOrganizeSettingsResponse(BaseModel):
    """Response for auto-organize master settings."""

    enabled: bool


class WatcherFolderResponse(BaseModel):
    """Single watched folder row."""

    id: str
    folder_path: str
    auto_organize_enabled: bool
    frequency_value: int
    frequency_unit: str
    frequency_seconds: int
    recursive: bool
    last_scanned_at: datetime | None = None
    next_scan_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class WatcherFoldersResponse(BaseModel):
    """List response for watched folders."""

    results: list[WatcherFolderResponse]


class OnboardingStatusResponse(BaseModel):
    """First-run onboarding and seeding status."""

    status: str
    onboarding_seeded: bool = False
    started_at: datetime | None = None
    seeded_at: datetime | None = None
    completed_at: datetime | None = None
    should_seed_defaults: bool


class LockSettingStatus(BaseModel):
    """App settings row status for one lock key."""

    key: str
    updated_at: datetime | None = None


class LockSettingsResponse(BaseModel):
    """Response for GET/PUT /api/settings/locks."""

    lock_file: list[str] = Field(default_factory=list)
    lock_folder: list[str] = Field(default_factory=list)
    lock_file_status: LockSettingStatus
    lock_folder_status: LockSettingStatus


# ── Search ───────────────────────────────────────────────────────────────


class FileSearchResultItem(BaseModel):
    """Single file row returned from POST /api/search/files."""

    id: str
    file_name: str
    file_type: str
    size_bytes: int
    folder: str
    last_edited: datetime
    path: str


class FileSearchResponse(BaseModel):
    """Response for POST /api/search/files."""

    results: list[FileSearchResultItem]
    semantic_status: Literal["ready", "pending", "degraded", "not_ready"] = "ready"
    semantic_error: str | None = None
    indexing_pending_count: int = 0


# ── Notes ────────────────────────────────────────────────────────────────


class NotesSummarizeResponse(BaseModel):
    """Response for POST /api/notes/summarize."""

    summary: str
    suggested_title: str | None = None
    processing_time_ms: int | None = None
