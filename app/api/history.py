"""
History API router (V3).

Read-only endpoints for the audit log.
Returns wrapped ``{"results": [...]}`` with enriched history entries.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import File, HistoryLog
from app.db.session import get_db
from app.models.response import (
    CategoryScoreResponse,
    HistoryListResponse,
    HistoryLogResponse,
)
from app.services.history_service import HistoryService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/history", tags=["history"])


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

    # Extract categories from metadata.all_scores (set by organize pipeline)
    raw_scores = metadata.get("all_scores", [])
    categories = [
        CategoryScoreResponse(
            category_id=s.get("category_id", ""),
            name=s.get("name", ""),
            score=round(float(s.get("score", 0)) * 100, 1),
        )
        for s in raw_scores
    ]

    # current_category = top-scored category name
    current_category: str | None = None
    if categories:
        current_category = categories[0].name

    # Resolve original_path from File table
    original_path: str | None = None
    file_record = await db.get(File, log.file_id)
    if file_record:
        original_path = file_record.original_path

    return HistoryLogResponse(
        id=log.id,
        file_id=log.file_id,
        action=log.action,
        categories=categories,
        current_category=current_category,
        original_path=original_path,
        moved_path=None,  # placeholder — move logic not yet implemented
        created_at=log.created_at,
    )


# ── Routes ───────────────────────────────────────────────────────────────


@router.get("", response_model=HistoryListResponse)
async def list_history(
    limit: int = Query(default=100, le=500),
    action: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    history_svc: HistoryService = Depends(_get_history),
) -> HistoryListResponse:
    """Get recent history entries, optionally filtered by action type."""
    logs = await history_svc.get_recent(db, limit=limit, action=action)

    results = []
    for log in logs:
        results.append(await _enrich_log(log, db))

    return HistoryListResponse(results=results)


@router.get("/file/{file_id}", response_model=HistoryListResponse)
async def get_file_history(
    file_id: str,
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
    history_svc: HistoryService = Depends(_get_history),
) -> HistoryListResponse:
    """Get history entries for a specific file."""
    logs = await history_svc.get_by_file(db, file_id=file_id, limit=limit)

    results = []
    for log in logs:
        results.append(await _enrich_log(log, db))

    return HistoryListResponse(results=results)


@router.get("/list")
async def get_mock_history_list() -> dict[str, list[dict[str, Any]]]:
    """Return UI-ready mock history rows for frontend development/testing."""

    return {"items": MOCK_HISTORY_ITEMS}
