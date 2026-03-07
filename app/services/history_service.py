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
        actions: list[str] | None = None,
    ) -> list[HistoryLog]:
        """Return history entries for a specific file (newest first)."""
        file_id_column = getattr(HistoryLog, "file_id")
        created_at_column = getattr(HistoryLog, "created_at")
        action_column = getattr(HistoryLog, "action")

        stmt = (
            select(HistoryLog)
            .where(file_id_column == file_id)
            .order_by(created_at_column.desc())
            .limit(limit)
        )
        if actions:
            stmt = stmt.where(action_column.in_(actions))

        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def get_recent(
        self,
        db: AsyncSession,
        limit: int = 100,
        action: str | None = None,
        actions: list[str] | None = None,
    ) -> list[HistoryLog]:
        """Return the most recent history entries, optionally filtered by action."""
        created_at_column = getattr(HistoryLog, "created_at")
        action_column = getattr(HistoryLog, "action")

        stmt = select(HistoryLog).order_by(created_at_column.desc()).limit(limit)
        if action:
            stmt = stmt.where(action_column == action)
        elif actions:
            stmt = stmt.where(action_column.in_(actions))
        result = await db.execute(stmt)
        return list(result.scalars().all())
