"""Integration tests for /api/organize endpoints."""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.response import (
    CategoryScoreResponse,
    OrganizeFileResult,
)

pytestmark = pytest.mark.integration


def _override_process(fastapi_app, builder) -> None:
    """Override organize_pipeline.process_single_file with a stub."""
    import app.api.organize as organize_module

    async def _fake_process_single_file(*, filepath, **kwargs) -> OrganizeFileResult:
        return builder(filepath)

    fastapi_app._patched_process_single_file = (
        organize_module.process_single_file,
        _fake_process_single_file,
    )
    organize_module.process_single_file = _fake_process_single_file  # type: ignore[assignment]


def _restore_process(fastapi_app) -> None:
    import app.api.organize as organize_module

    if hasattr(fastapi_app, "_patched_process_single_file"):
        original, _ = fastapi_app._patched_process_single_file
        organize_module.process_single_file = original  # type: ignore[assignment]
        del fastapi_app._patched_process_single_file


def test_organize_returns_results_keyed_by_filepath(fastapi_app, test_client) -> None:
    def _builder(path):
        return OrganizeFileResult(
            file_id="file-1",
            suggested_names=["renamed.pdf"],
            categories=[CategoryScoreResponse(category_id="c1", name="Work", score=88.0)],
        )

    _override_process(fastapi_app, _builder)
    try:
        response = test_client.post(
            "/api/organize",
            json={"file_paths": ["/tmp/a.pdf"], "force": False},
        )
    finally:
        _restore_process(fastapi_app)

    assert response.status_code == 200
    payload = response.json()
    assert "/tmp/a.pdf" in payload["results"]
    file_result = payload["results"]["/tmp/a.pdf"]
    assert file_result["file_id"] == "file-1"
    assert file_result["suggested_names"] == ["renamed.pdf"]
    assert file_result["categories"][0]["name"] == "Work"


def test_organize_rejects_empty_file_paths(test_client) -> None:
    response = test_client.post("/api/organize", json={"file_paths": []})
    assert response.status_code == 422


def test_organize_locked_file_short_circuits_pipeline(fastapi_app, test_client) -> None:
    # Seed lock setting that matches /tmp/locked.pdf
    import asyncio

    from app.db.session import engine
    from app.db.models import AppSetting

    async def _seed_lock():
        async with AsyncSession(engine, expire_on_commit=False) as db:
            db.add(
                AppSetting(
                    key="lock_file",
                    value=json.dumps(["/tmp/locked.pdf"]),
                )
            )
            await db.commit()

    asyncio.run(_seed_lock())

    called = {"count": 0}

    def _builder(path):
        called["count"] += 1
        return OrganizeFileResult(
            file_id="should-not-run",
            suggested_names=[],
            categories=[],
        )

    _override_process(fastapi_app, _builder)
    try:
        response = test_client.post(
            "/api/organize",
            json={"file_paths": ["/tmp/locked.pdf"]},
        )
    finally:
        _restore_process(fastapi_app)

    assert response.status_code == 200
    body = response.json()
    locked_result = body["results"]["/tmp/locked.pdf"]
    # build_locked_result returns an error message; pipeline must be skipped
    assert called["count"] == 0
    assert locked_result["error"] is not None
    assert "lock" in locked_result["error"].lower()


def test_apply_organize_decision_404_when_file_missing(test_client) -> None:
    response = test_client.post(
        "/api/organize/apply",
        json={"file_id": "nonexistent", "selected_name": "x.pdf"},
    )
    assert response.status_code == 404


def test_apply_organize_decision_422_when_no_action_provided(test_client) -> None:
    response = test_client.post(
        "/api/organize/apply",
        json={"file_id": "file-1"},
    )
    # Validator raises ValueError → 422 from pydantic
    assert response.status_code == 422


def test_apply_organize_decision_renames_and_logs_history(fastapi_app, test_client) -> None:
    """End-to-end: seed a file, apply rename, verify file row and history log."""
    import asyncio

    from app.db.models import File, HistoryLog
    from app.db.session import engine
    from sqlmodel import select

    file_id = "file-apply-1"

    async def _seed():
        async with AsyncSession(engine, expire_on_commit=False) as db:
            db.add(
                File(
                    id=file_id,
                    original_path="/tmp/source/original.pdf",
                    current_path="/tmp/source/original.pdf",
                    hash="h1",
                    size=1,
                    extension=".pdf",
                )
            )
            await db.commit()

    asyncio.run(_seed())

    response = test_client.post(
        "/api/organize/apply",
        json={"file_id": file_id, "selected_name": "renamed.pdf"},
    )
    assert response.status_code == 200
    assert response.json() == {"success": True}

    async def _verify():
        async with AsyncSession(engine, expire_on_commit=False) as db:
            file_row = await db.get(File, file_id)
            assert file_row is not None
            assert file_row.current_path.endswith("/renamed.pdf")
            logs = await db.execute(
                select(HistoryLog).where(HistoryLog.file_id == file_id)
            )
            history = list(logs.scalars().all())
            assert len(history) == 1
            assert history[0].action == "renamed"

    asyncio.run(_verify())
