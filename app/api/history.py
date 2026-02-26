"""
History API router.

Read-only endpoints for the audit log.
"""

import json
import logging

from fastapi import APIRouter, Depends, Query
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.session import get_db
from app.models.response import HistoryLogResponse
from app.services.history_service import HistoryService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/history", tags=["history"])


# ── Dependency Injection ─────────────────────────────────────────────────


def _get_history() -> HistoryService:
    return HistoryService()


# ── Routes ───────────────────────────────────────────────────────────────


@router.get("", response_model=list[HistoryLogResponse])
async def list_history(
    limit: int = Query(default=100, le=500),
    action: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    history_svc: HistoryService = Depends(_get_history),
) -> list[HistoryLogResponse]:
    """Get recent history entries, optionally filtered by action type."""
    logs = await history_svc.get_recent(db, limit=limit, action=action)
    return [
        HistoryLogResponse(
            id=log.id,
            file_id=log.file_id,
            action=log.action,
            metadata=json.loads(log.metadata_json) if log.metadata_json else None,
            created_at=log.created_at,
        )
        for log in logs
    ]


@router.get("/file/{file_id}", response_model=list[HistoryLogResponse])
async def get_file_history(
    file_id: str,
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
    history_svc: HistoryService = Depends(_get_history),
) -> list[HistoryLogResponse]:
    """Get history entries for a specific file."""
    logs = await history_svc.get_by_file(db, file_id=file_id, limit=limit)
    return [
        HistoryLogResponse(
            id=log.id,
            file_id=log.file_id,
            action=log.action,
            metadata=json.loads(log.metadata_json) if log.metadata_json else None,
            created_at=log.created_at,
        )
        for log in logs
    ]
