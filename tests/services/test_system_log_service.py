"""Tests for SystemLogService — operational event logging and cleanup."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import select

from app.db.models import SystemLog
from app.services.system_log_service import SystemLogService

pytestmark = pytest.mark.unit


async def test_log_persists_event_with_normalized_level(async_session) -> None:
    svc = SystemLogService()
    entry = await svc.log(
        db=async_session,
        level="warning",
        component="test",
        event_type="something_happened",
        message="hello",
        context={"key": "value"},
    )
    assert entry.level == "WARNING"
    assert entry.component == "test"
    assert entry.event_type == "something_happened"
    assert '"key": "value"' in (entry.context_json or "")


async def test_log_captures_correlation_id_from_trace_context(
    async_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import system_log_service as module

    monkeypatch.setattr(module, "get_current_trace_id", lambda: "trace-xyz")

    svc = SystemLogService()
    entry = await svc.log(
        db=async_session,
        level="INFO",
        component="lifecycle",
        event_type="startup",
        message="ok",
    )
    assert entry.correlation_id == "trace-xyz"


async def test_log_explicit_correlation_id_wins(async_session) -> None:
    svc = SystemLogService()
    entry = await svc.log(
        db=async_session,
        level="INFO",
        component="test",
        event_type="e",
        message="m",
        correlation_id="explicit-id",
    )
    assert entry.correlation_id == "explicit-id"


async def test_cleanup_old_logs_deletes_only_older_than_retention(async_session) -> None:
    now = datetime.now(timezone.utc)
    stale = SystemLog(
        level="INFO",
        component="x",
        event_type="e",
        message="m",
        created_at=now - timedelta(days=10),
    )
    fresh = SystemLog(
        level="INFO",
        component="x",
        event_type="e",
        message="m",
        created_at=now - timedelta(days=1),
    )
    async_session.add_all([stale, fresh])
    await async_session.flush()

    svc = SystemLogService()
    deleted = await svc.cleanup_old_logs(async_session, retention_days=5)
    assert deleted == 1

    result = await async_session.execute(select(SystemLog))
    remaining = list(result.scalars().all())
    assert len(remaining) == 1
    assert remaining[0].id == fresh.id


async def test_cleanup_old_logs_with_nothing_stale_returns_zero(async_session) -> None:
    svc = SystemLogService()
    deleted = await svc.cleanup_old_logs(async_session, retention_days=30)
    assert deleted == 0


async def test_get_recent_filters_by_level_and_component(async_session) -> None:
    svc = SystemLogService()
    await svc.log(
        db=async_session,
        level="WARNING",
        component="lifecycle",
        event_type="rag_startup_failed",
        message="boom",
    )
    await svc.log(
        db=async_session,
        level="INFO",
        component="lifecycle",
        event_type="ok",
        message="ok",
    )
    await svc.log(
        db=async_session,
        level="WARNING",
        component="other",
        event_type="x",
        message="x",
    )

    warnings_lifecycle = await svc.get_recent(
        async_session,
        level="warning",
        component="lifecycle",
    )
    assert len(warnings_lifecycle) == 1
    assert warnings_lifecycle[0].event_type == "rag_startup_failed"
