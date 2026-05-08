"""Calendar Events API router.

Reads pending detected events and accepts approve/reject status updates from
the frontend. Approval flow: the frontend calls Google Calendar directly,
then PATCHes here with the resulting google_event_id.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import DetectedCalendarEvent, File
from app.db.session import get_db
from app.services.history_service import HistoryService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/calendar-events", tags=["calendar-events"])


# ── Schemas ──────────────────────────────────────────────────────────────


class CalendarEventPayload(BaseModel):
    title: str = ""
    start_iso: str = ""
    end_iso: str = ""
    all_day: bool = False
    location: str = ""
    attendees: list[str] = Field(default_factory=list)
    description: str = ""
    confidence: float = 0.0


class DetectedCalendarEventResponse(BaseModel):
    id: str
    file_id: str
    file_name: str
    source_path: str
    event: CalendarEventPayload
    status: str
    detected_at: str
    google_event_id: Optional[str] = None


class PendingCalendarEventsResponse(BaseModel):
    items: list[DetectedCalendarEventResponse]


class UpdateStatusRequest(BaseModel):
    status: Literal["approved", "rejected"]
    google_event_id: Optional[str] = None


class UpdateStatusResponse(BaseModel):
    id: str
    status: str
    status_changed_at: Optional[str]
    google_event_id: Optional[str] = None


# ── Helpers ──────────────────────────────────────────────────────────────


def _get_history() -> HistoryService:
    return HistoryService()


def _parse_event_payload(event_json: str | None) -> CalendarEventPayload:
    if not event_json:
        return CalendarEventPayload()
    try:
        return CalendarEventPayload.model_validate(json.loads(event_json))
    except Exception:  # noqa: BLE001 — surface a default rather than 500 the list
        return CalendarEventPayload()


def _to_response(
    row: DetectedCalendarEvent,
    file_record: File | None,
) -> DetectedCalendarEventResponse:
    source_path = (file_record.current_path if file_record else "") or ""
    file_name = Path(source_path).name if source_path else ""
    return DetectedCalendarEventResponse(
        id=row.id,
        file_id=row.file_id,
        file_name=file_name,
        source_path=source_path,
        event=_parse_event_payload(row.event_json),
        status=row.status,
        detected_at=row.detected_at.isoformat(),
        google_event_id=row.google_event_id,
    )


# ── Routes ───────────────────────────────────────────────────────────────


@router.get("/pending", response_model=PendingCalendarEventsResponse)
async def list_pending(
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> PendingCalendarEventsResponse:
    """Return up to ``limit`` pending detected calendar events, newest first."""
    detected_at_col = getattr(DetectedCalendarEvent, "detected_at")
    status_col = getattr(DetectedCalendarEvent, "status")
    stmt = (
        select(DetectedCalendarEvent)
        .where(status_col == "pending")
        .order_by(detected_at_col.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    rows = list(result.scalars().all())

    if not rows:
        return PendingCalendarEventsResponse(items=[])

    file_ids = list({row.file_id for row in rows})
    file_id_col = getattr(File, "id")
    file_result = await db.execute(select(File).where(file_id_col.in_(file_ids)))
    file_map = {f.id: f for f in file_result.scalars().all()}

    items = [_to_response(row, file_map.get(row.file_id)) for row in rows]
    return PendingCalendarEventsResponse(items=items)


@router.patch("/{event_id}", response_model=UpdateStatusResponse)
async def update_status(
    event_id: str,
    body: UpdateStatusRequest,
    db: AsyncSession = Depends(get_db),
    history_svc: HistoryService = Depends(_get_history),
) -> UpdateStatusResponse:
    row = await db.get(DetectedCalendarEvent, event_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Calendar event not found.")

    if body.status == "approved" and not body.google_event_id:
        raise HTTPException(
            status_code=400,
            detail="google_event_id is required when status=approved.",
        )

    row.status = body.status
    row.status_changed_at = datetime.now(timezone.utc)
    if body.status == "approved" and body.google_event_id:
        row.google_event_id = body.google_event_id
    await db.flush()

    event_payload = _parse_event_payload(row.event_json)
    file_record = await db.get(File, row.file_id)
    source_path = ""
    if file_record:
        source_path = file_record.current_path or file_record.original_path or ""
    source_file_name = Path(source_path).name if source_path else ""

    description = event_payload.description or ""
    if len(description) > 2000:
        description = description[:2000]

    if event_payload.all_day:
        meeting_time = event_payload.start_iso or ""
    else:
        meeting_time = event_payload.start_iso or ""
        if event_payload.end_iso:
            meeting_time = (
                f"{meeting_time} – {event_payload.end_iso}"
                if meeting_time
                else event_payload.end_iso
            )

    metadata: dict[str, Any] = {
        "calendar_event_id": row.id,
        "status": row.status,
        "meeting_title": event_payload.title,
        "meeting_time": meeting_time,
        "meeting_location": event_payload.location,
        "details": description,
        "attendees": list(event_payload.attendees or []),
        "source_file_name": source_file_name,
        "found_in_file": True,
        "all_day": event_payload.all_day,
        "start_iso": event_payload.start_iso,
        "end_iso": event_payload.end_iso,
        "action_label": "Open in Google Calendar" if body.status == "approved" else "Dismissed",
    }
    if row.google_event_id:
        metadata["google_event_id"] = row.google_event_id

    await history_svc.log(
        db=db,
        file_id=row.file_id,
        action=(
            "calendar_event_approved"
            if body.status == "approved"
            else "calendar_event_rejected"
        ),
        metadata=metadata,
    )

    return UpdateStatusResponse(
        id=row.id,
        status=row.status,
        status_changed_at=row.status_changed_at.isoformat()
        if row.status_changed_at
        else None,
        google_event_id=row.google_event_id,
    )
