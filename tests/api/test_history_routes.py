"""Integration tests for /api/history endpoints."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.integration


def _seed_history(fastapi_app) -> dict:
    """Insert a File + HistoryLog directly via the app engine."""
    import asyncio

    from sqlmodel.ext.asyncio.session import AsyncSession

    from app.db.models import File, HistoryLog
    from app.db.session import engine

    file_id = "file-history-1"
    log_id = "log-history-1"

    async def _seed():
        async with AsyncSession(engine, expire_on_commit=False) as db:
            db.add(
                File(
                    id=file_id,
                    original_path="/tmp/source/a.pdf",
                    current_path="/tmp/source/a.pdf",
                    hash="h-history",
                    size=10,
                    extension=".pdf",
                )
            )
            db.add(
                HistoryLog(
                    id=log_id,
                    file_id=file_id,
                    action="renamed",
                    metadata_json=json.dumps(
                        {
                            "file_name": "renamed.pdf",
                            "source_path": "/tmp/source/a.pdf",
                            "new_path": "/tmp/source/renamed.pdf",
                            "selected_category": {
                                "id": "cat-finance",
                                "name": "Finance",
                                "score": 91.0,
                            },
                        }
                    ),
                    created_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
                )
            )
            await db.commit()

    asyncio.run(_seed())
    return {"file_id": file_id, "log_id": log_id}


def test_list_history_returns_user_actions_only(fastapi_app, test_client) -> None:
    _seed_history(fastapi_app)
    response = test_client.get("/api/history")
    assert response.status_code == 200
    body = response.json()
    assert body["limit"] == 20
    assert body["offset"] == 0
    assert body["has_more"] is False
    assert len(body["results"]) == 1
    entry = body["results"][0]
    assert entry["action"] == "renamed"
    assert entry["file_name"] == "renamed.pdf"
    assert entry["category"]["name"] == "Finance"
    assert entry["category"]["score"] == 91.0


def test_list_history_filters_by_action(fastapi_app, test_client) -> None:
    _seed_history(fastapi_app)
    response = test_client.get("/api/history", params={"action": "moved"})
    assert response.status_code == 200
    # Only "renamed" exists, so filtering by moved returns nothing.
    assert response.json()["results"] == []


def test_list_history_pagination_respects_limit_param(
    fastapi_app,
    test_client,
) -> None:
    _seed_history(fastapi_app)
    response = test_client.get("/api/history", params={"limit": 1, "offset": 0})
    assert response.status_code == 200
    body = response.json()
    assert body["limit"] == 1
    assert len(body["results"]) <= 1


def test_file_history_endpoint(fastapi_app, test_client) -> None:
    seeded = _seed_history(fastapi_app)
    response = test_client.get(f"/api/history/file/{seeded['file_id']}")
    assert response.status_code == 200
    body = response.json()
    assert len(body["results"]) == 1
    assert body["results"][0]["action"] == "renamed"


def test_create_note_history_inserts_log(fastapi_app, test_client) -> None:
    response = test_client.post(
        "/api/history/note",
        json={
            "file_name": "notes.md",
            "destination_path": "/tmp/output/notes.md",
            "source_files": ["/tmp/a.txt"],
            "category_name": "Work",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["action"] == "note"
    assert body["id"]


def test_mock_history_list(test_client) -> None:
    response = test_client.get("/api/history/list")
    assert response.status_code == 200
    body = response.json()
    assert "items" in body
    assert len(body["items"]) >= 1


def test_list_history_rejects_invalid_limit(test_client) -> None:
    response = test_client.get("/api/history", params={"limit": 0})
    assert response.status_code == 422
    response = test_client.get("/api/history", params={"limit": 999999})
    assert response.status_code == 422
