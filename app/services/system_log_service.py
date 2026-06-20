"""
System Log Service — operational log events stored in SQLite.

Separate from `history_logs`, which represent per-file audit history.
`system_logs` is intended for application lifecycle, warnings, and failures
that are useful for troubleshooting and monitoring.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlmodel import delete, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import SystemLog
from app.observability.tracing import get_current_trace_id

logger = logging.getLogger(__name__)


class SystemLogService:
    """Append-only operational logging backed by SQLite."""

    async def log(
        self,
        db: AsyncSession,
        *,
        level: str,
        component: str,
        event_type: str,
        message: str,
        context: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> SystemLog:
        """Record a structured operational event."""
        correlation_id = correlation_id or get_current_trace_id()
        entry = SystemLog(
            level=level.upper(),
            component=component,
            event_type=event_type,
            message=message,
            context_json=json.dumps(context, default=str) if context else None,
            correlation_id=correlation_id,
        )
        db.add(entry)
        await db.flush()
        logger.debug("SystemLog: %s %s", entry.level, entry.event_type)
        return entry

    async def cleanup_old_logs(
        self,
        db: AsyncSession,
        *,
        retention_days: int,
    ) -> int:
        """Delete operational logs older than the configured retention period."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        created_at_column = getattr(SystemLog, "created_at")
        id_column = getattr(SystemLog, "id")

        result = await db.execute(
            select(id_column).where(created_at_column < cutoff)
        )
        stale_ids = list(result.scalars().all())
        if not stale_ids:
            return 0

        await db.execute(
            delete(SystemLog).where(id_column.in_(stale_ids))
        )
        return len(stale_ids)

    async def get_recent(
        self,
        db: AsyncSession,
        *,
        limit: int = 100,
        level: str | None = None,
        component: str | None = None,
    ) -> list[SystemLog]:
        """Return recent system logs for future diagnostics tooling."""
        created_at_column = getattr(SystemLog, "created_at")
        level_column = getattr(SystemLog, "level")
        component_column = getattr(SystemLog, "component")

        stmt = select(SystemLog).order_by(created_at_column.desc()).limit(limit)
        if level:
            stmt = stmt.where(level_column == level.upper())
        if component:
            stmt = stmt.where(component_column == component)
        result = await db.execute(stmt)
        return list(result.scalars().all())
