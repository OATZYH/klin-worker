"""Tests for HistoryService — audit-log persistence and pagination."""

from __future__ import annotations

import json

import pytest

from app.db.models import File, HistoryLog
from app.services.history_service import HistoryService

pytestmark = pytest.mark.unit


async def _make_file(db, *, file_id: str = "f-1", path: str = "/tmp/x.pdf") -> File:
    row = File(
        id=file_id,
        original_path=path,
        current_path=path,
        hash="h-" + file_id,
        size=1,
        extension=".pdf",
    )
    db.add(row)
    await db.flush()
    return row


async def test_log_inserts_row_with_metadata(async_session) -> None:
    await _make_file(async_session)
    svc = HistoryService()

    entry = await svc.log(
        db=async_session,
        file_id="f-1",
        action="renamed",
        metadata={"new_name": "doc.pdf"},
    )

    assert entry.id
    assert entry.action == "renamed"
    assert json.loads(entry.metadata_json) == {"new_name": "doc.pdf"}


async def test_log_omits_metadata_when_none(async_session) -> None:
    await _make_file(async_session)
    svc = HistoryService()
    entry = await svc.log(db=async_session, file_id="f-1", action="moved")
    assert entry.metadata_json is None


async def test_get_by_file_returns_newest_first(async_session) -> None:
    await _make_file(async_session)
    svc = HistoryService()

    for action in ["renamed", "moved", "note"]:
        await svc.log(db=async_session, file_id="f-1", action=action)

    logs = await svc.get_by_file(async_session, file_id="f-1", limit=10)
    assert [log.action for log in logs] == ["note", "moved", "renamed"]


async def test_get_by_file_filters_by_actions(async_session) -> None:
    await _make_file(async_session)
    svc = HistoryService()
    await svc.log(db=async_session, file_id="f-1", action="renamed")
    await svc.log(db=async_session, file_id="f-1", action="ignored")

    logs = await svc.get_by_file(
        async_session,
        file_id="f-1",
        limit=10,
        actions=["renamed"],
    )
    assert [log.action for log in logs] == ["renamed"]


async def test_get_recent_page_pagination_and_has_more(async_session) -> None:
    await _make_file(async_session)
    svc = HistoryService()
    for index in range(5):
        await svc.log(db=async_session, file_id="f-1", action=f"action-{index}")

    page1, has_more1 = await svc.get_recent_page(async_session, limit=2, offset=0)
    page2, has_more2 = await svc.get_recent_page(async_session, limit=2, offset=2)
    page3, has_more3 = await svc.get_recent_page(async_session, limit=2, offset=4)

    assert len(page1) == 2 and has_more1 is True
    assert len(page2) == 2 and has_more2 is True
    assert len(page3) == 1 and has_more3 is False


async def test_get_recent_page_filters_by_single_action(async_session) -> None:
    await _make_file(async_session)
    svc = HistoryService()
    await svc.log(db=async_session, file_id="f-1", action="renamed")
    await svc.log(db=async_session, file_id="f-1", action="moved")
    await svc.log(db=async_session, file_id="f-1", action="note")

    logs, _ = await svc.get_recent_page(
        async_session,
        limit=10,
        offset=0,
        action="moved",
    )
    assert [log.action for log in logs] == ["moved"]


async def test_get_recent_page_search_matches_metadata(async_session) -> None:
    await _make_file(async_session)
    svc = HistoryService()
    await svc.log(
        db=async_session,
        file_id="f-1",
        action="note",
        metadata={"file_name": "summary-alpha.md"},
    )
    await svc.log(
        db=async_session,
        file_id="f-1",
        action="note",
        metadata={"file_name": "other.md"},
    )

    logs, _ = await svc.get_recent_page(async_session, limit=10, search="alpha")
    assert len(logs) == 1
    assert "alpha" in (logs[0].metadata_json or "")
