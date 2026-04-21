"""
History Service — audit log for all file operations.

Every action (scan, categorize, rename-suggest, etc.) is recorded
in the `history_logs` table for auditability.
"""

import json
import logging
from typing import Any

from sqlalchemy import or_
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import File, HistoryLog
from app.observability.tracing import observe

logger = logging.getLogger(__name__)


class HistoryService:
    """Append-only audit log backed by SQLite."""

    # ── Write ────────────────────────────────────────────────────────────

    @observe(name="history.log", capture_input=False, capture_output=False)
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

    @observe(name="history.get_by_file", capture_input=False, capture_output=False)
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

    @observe(name="history.get_recent", capture_input=False, capture_output=False)
    async def get_recent(
        self,
        db: AsyncSession,
        limit: int = 100,
        action: str | None = None,
        actions: list[str] | None = None,
    ) -> list[HistoryLog]:
        """Return the most recent history entries, optionally filtered by action."""
        logs, _ = await self.get_recent_page(
            db=db,
            limit=limit,
            offset=0,
            action=action,
            actions=actions,
            search=None,
        )
        return logs

    @observe(name="history.get_recent_page", capture_input=False, capture_output=False)
    async def get_recent_page(
        self,
        db: AsyncSession,
        limit: int = 20,
        offset: int = 0,
        action: str | None = None,
        actions: list[str] | None = None,
        search: str | None = None,
    ) -> tuple[list[HistoryLog], bool]:
        """Return paginated recent history entries plus a has_more flag."""
        normalized_limit = max(1, min(limit, 500))
        normalized_offset = max(0, offset)

        stmt = (
            select(HistoryLog)
            .outerjoin(File, File.id == HistoryLog.file_id)
            .order_by(HistoryLog.created_at.desc())
        )

        if action:
            stmt = stmt.where(HistoryLog.action == action)
        elif actions:
            stmt = stmt.where(HistoryLog.action.in_(actions))

        if search:
            token = f"%{search.strip()}%"
            stmt = stmt.where(
                or_(
                    HistoryLog.action.ilike(token),
                    HistoryLog.metadata_json.ilike(token),
                    File.original_path.ilike(token),
                )
            )

        stmt = stmt.offset(normalized_offset).limit(normalized_limit + 1)
        result = await db.execute(stmt)
        logs = list(result.scalars().all())
        has_more = len(logs) > normalized_limit

        if has_more:
            logs = logs[:normalized_limit]

        return logs, has_more
