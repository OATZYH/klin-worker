"""
History API router (V3).

Read-only endpoints for the audit log.
Returns wrapped ``{"results": [...]}`` with enriched history entries.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any
from pathlib import Path
import hashlib

from fastapi import APIRouter, Depends, Query
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import File, HistoryLog
from app.db.session import get_db
from app.models.request import NoteHistoryCreateRequest
from app.models.response import (
    HistoryListResponse,
    HistoryLogResponse,
    SelectedCategoryScoreResponse,
)
from app.services.history_service import HistoryService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/history", tags=["history"])

USER_ACTIONS = ["renamed", "moved", "renamed_moved", "note"]


MOCK_HISTORY_ITEMS: list[dict[str, Any]] = [
    {
        "id": "h1",
        "type": "organize",
        "title": "Invoice-2026-02.pdf",
        "subtitle": "Moved to Finance",
        "timestamp": datetime(2026, 3, 7, 5, 0, tzinfo=timezone.utc).isoformat(),
        "fromPath": "C:/Users/supak/Downloads/Invoice-2026-02.pdf",
        "toPath": "C:/Users/supak/Documents/KLIN/Finance/Invoice-2026-02.pdf",
        "oldName": "invoice_2026_02.pdf",
        "newName": "Invoice-2026-02.pdf",
        "scores": [
            {"name": "Finance", "score": 0.91},
            {"name": "Work", "score": 0.06},
            {"name": "Personal", "score": 0.03},
        ],
    },
    {
        "id": "h2",
        "type": "summary",
        "title": "Project-Alpha-Summary.md",
        "subtitle": "Summary generated",
        "timestamp": datetime(2026, 3, 7, 4, 20, tzinfo=timezone.utc).isoformat(),
        "fileNames": ["meeting-notes.txt", "action-items.txt", "timeline.txt"],
        "summaryPath": "C:/Users/supak/Documents/KLIN/Summaries/Project-Alpha-Summary.md",
    },
    {
        "id": "h3",
        "type": "calendar",
        "title": "Project Alpha Weekly Sync",
        "subtitle": "Calendar event found in notes",
        "timestamp": datetime(2026, 3, 7, 3, 45, tzinfo=timezone.utc).isoformat(),
        "foundInFile": True,
        "sourceFileName": "meeting-notes.txt",
        "meetingTitle": "Project Alpha Weekly Sync",
        "meetingTime": "2026-03-08 10:00",
        "meetingLocation": "Microsoft Teams",
        "details": "Review sprint progress and pending blockers.",
        "actionLabel": "Add to calendar",
    },
]


# ── Dependency Injection ─────────────────────────────────────────────────


def _get_history() -> HistoryService:
    return HistoryService()


# ── Helpers ──────────────────────────────────────────────────────────────


async def _enrich_log(log: HistoryLog, db: AsyncSession) -> HistoryLogResponse:
    """Convert a raw HistoryLog row into a V3 HistoryLogResponse."""
    metadata: dict = {}
    if log.metadata_json:
        try:
            metadata = json.loads(log.metadata_json)
        except (json.JSONDecodeError, TypeError):
            pass

    category: SelectedCategoryScoreResponse | None = None
    raw_category = metadata.get("selected_category") or metadata.get("category")
    if isinstance(raw_category, dict):
        category_id = raw_category.get("id") or raw_category.get("category_id")
        category_name = raw_category.get("name")
        raw_score = raw_category.get("score")
        category_score: float | None = None
        if isinstance(raw_score, int | float):
            category_score = float(raw_score)
        elif isinstance(raw_score, str):
            try:
                category_score = float(raw_score)
            except ValueError:
                category_score = None
        if category_id and category_name:
            category = SelectedCategoryScoreResponse(
                id=str(category_id),
                name=str(category_name),
                score=category_score,
            )

    source_path = metadata.get("source_path")
    new_path = (
        metadata.get("new_path")
        or metadata.get("destination_path")
        or metadata.get("renamed_path")
        or metadata.get("moved_path")
    )
    file_name = str(metadata.get("file_name") or "")
    source_files = metadata.get("source_files") if isinstance(metadata.get("source_files"), list) else None
    category_name = metadata.get("category_name") if isinstance(metadata.get("category_name"), str) else None

    file_record = await db.get(File, log.file_id)
    current_path: str | None = None
    if file_record:
        current_path = file_record.current_path

    if not file_name:
        path_for_name = new_path or current_path
        if path_for_name:
            file_name = Path(path_for_name).name

    original_path = str(source_path or (file_record.original_path if file_record else "")) or None

    return HistoryLogResponse(
        id=log.id,
        file_id=log.file_id,
        action=log.action,
        file_name=file_name,
        category=category,
        original_path=original_path,
        new_path=str(new_path) if new_path else None,
        source_files=[str(item) for item in source_files] if source_files else None,
        category_name=category_name,
        created_at=log.created_at,
    )


async def _get_or_create_note_file(db: AsyncSession, destination_path: str) -> File:
    """Reuse existing file row for destination path or create a minimal one."""
    path = destination_path.strip()
    existing_stmt = select(File).where((File.current_path == path) | (File.original_path == path))
    existing = await db.execute(existing_stmt)
    file_record = existing.scalars().first()
    if file_record:
        return file_record

    hashed = hashlib.sha256(path.encode("utf-8")).hexdigest()
    extension = Path(path).suffix or ".md"
    new_file = File(
        original_path=path,
        current_path=path,
        hash=hashed,
        size=0,
        extension=extension,
    )
    db.add(new_file)
    await db.flush()
    return new_file


# ── Routes ───────────────────────────────────────────────────────────────


@router.get("", response_model=HistoryListResponse)
async def list_history(
    limit: int = Query(default=20, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    action: str | None = Query(default=None),
    search: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    history_svc: HistoryService = Depends(_get_history),
) -> HistoryListResponse:
    """Get paginated recent history entries, optionally filtered by action and search."""
    logs, has_more = await history_svc.get_recent_page(
        db,
        limit=limit,
        offset=offset,
        action=action,
        actions=None if action else USER_ACTIONS,
        search=search,
    )

    results = []
    for log in logs:
        results.append(await _enrich_log(log, db))

    return HistoryListResponse(results=results, limit=limit, offset=offset, has_more=has_more)


@router.get("/file/{file_id}", response_model=HistoryListResponse)
async def get_file_history(
    file_id: str,
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
    history_svc: HistoryService = Depends(_get_history),
) -> HistoryListResponse:
    """Get history entries for a specific file."""
    logs = await history_svc.get_by_file(db, file_id=file_id, limit=limit, actions=USER_ACTIONS)

    results = []
    for log in logs:
        results.append(await _enrich_log(log, db))

    return HistoryListResponse(results=results, limit=limit, offset=0, has_more=False)


@router.post("/note")
async def create_note_history(
    body: NoteHistoryCreateRequest,
    db: AsyncSession = Depends(get_db),
    history_svc: HistoryService = Depends(_get_history),
) -> dict[str, Any]:
    """Record a note save action in history using action='note'."""
    destination_path = body.destination_path.strip()
    file_record = await _get_or_create_note_file(db, destination_path)

    metadata = {
        "file_name": body.file_name.strip(),
        "destination_path": destination_path,
        "new_path": destination_path,
        "source_files": [str(item) for item in body.source_files if str(item).strip()],
        "category_name": body.category_name.strip() if isinstance(body.category_name, str) and body.category_name.strip() else None,
    }

    history = await history_svc.log(
        db=db,
        file_id=file_record.id,
        action="note",
        metadata=metadata,
    )
    await db.commit()

    return {
        "success": True,
        "id": history.id,
        "action": history.action,
    }


@router.get("/list")
async def get_mock_history_list() -> dict[str, list[dict[str, Any]]]:
    """Return UI-ready mock history rows for frontend development/testing."""

    return {"items": MOCK_HISTORY_ITEMS}
