"""
History API router (V3).

Read-only endpoints for the audit log.
Returns wrapped ``{"results": [...]}`` with enriched history entries.
"""

import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import File, HistoryLog
from app.db.session import get_db
from app.models.response import (
    HistoryListResponse,
    HistoryLogResponse,
    SelectedCategoryScoreResponse,
)
from app.services.history_service import HistoryService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/history", tags=["history"])

USER_ACTIONS = ["renamed", "moved", "renamed_moved"]


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
    new_path = metadata.get("new_path") or metadata.get("renamed_path") or metadata.get("moved_path")
    file_name = str(metadata.get("file_name") or "")

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
    logs = await history_svc.get_recent(
        db,
        limit=limit,
        action=action,
        actions=None if action else USER_ACTIONS,
    )

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
    logs = await history_svc.get_by_file(db, file_id=file_id, limit=limit, actions=USER_ACTIONS)

    results = []
    for log in logs:
        results.append(await _enrich_log(log, db))

    return HistoryListResponse(results=results)
