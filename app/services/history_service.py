"""
History Service — audit log for all file operations.

Every action (scan, categorize, rename-suggest, etc.) is recorded
in the `history_logs` table for auditability.
"""

import json
import logging
from typing import Any

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import HistoryLog

logger = logging.getLogger(__name__)


class HistoryService:
    """Append-only audit log backed by SQLite."""

    # ── Write ────────────────────────────────────────────────────────────

    async def log(
        self,
        db: AsyncSession,
        file_id: str,
        action: str,
        metadata: dict[str, Any] | None = None,
    ) -> HistoryLog:
        """Record an action in the history log."""
        entry = HistoryLog(
            file_id=file_id,
            action=action,
            metadata_json=json.dumps(metadata) if metadata else None,
        )
        db.add(entry)
        await db.flush()  # assign id
        logger.debug("History: %s → %s", action, file_id)
        return entry

    # ── Read ─────────────────────────────────────────────────────────────

    async def get_by_file(
        self,
        db: AsyncSession,
        file_id: str,
        limit: int = 50,
    ) -> list[HistoryLog]:
        """Return history entries for a specific file (newest first)."""
        result = await db.execute(
            select(HistoryLog)
            .where(HistoryLog.file_id == file_id)
            .order_by(HistoryLog.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_recent(
        self,
        db: AsyncSession,
        limit: int = 100,
        action: str | None = None,
    ) -> list[HistoryLog]:
        """Return the most recent history entries, optionally filtered by action."""
        stmt = select(HistoryLog).order_by(HistoryLog.created_at.desc()).limit(limit)
        if action:
            stmt = stmt.where(HistoryLog.action == action)
        result = await db.execute(stmt)
        return list(result.scalars().all())
