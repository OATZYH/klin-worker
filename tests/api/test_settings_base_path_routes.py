"""Integration tests for /api/settings/default-base-path."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

pytestmark = pytest.mark.integration


def test_get_default_base_path_returns_none_when_unset(test_client) -> None:
    response = test_client.get("/api/settings/default-base-path")
    assert response.status_code == 200
    assert response.json()["default_base_path"] is None


def test_put_default_base_path_rejects_nonexistent_parent(test_client) -> None:
    response = test_client.put(
        "/api/settings/default-base-path",
        json={"default_base_path": "/no/such/place/exists/KlinFiles"},
    )
    assert response.status_code == 400
    assert "Parent directory" in response.json()["detail"]


def test_put_default_base_path_persists_and_auto_updates_categories(
    fastapi_app,
    test_client,
    tmp_path: Path,
) -> None:
    """
    The PUT endpoint imports `get_rag_service` lazily for seeding.  When no
    categories exist, it falls back to the lazy path; we sidestep the
    embedding generation by pre-seeding one category that's *not* marked
    manual, then verify its destination_path gets overwritten.
    """
    import asyncio

    from app.db.models import Category
    from app.db.session import engine

    async def _seed_one_category():
        async with AsyncSession(engine, expire_on_commit=False) as db:
            db.add(
                Category(
                    id="cat-non-manual",
                    name="Inbox",
                    description="auto",
                    color="#111111",
                    is_active=True,
                    is_path_manual=False,
                )
            )
            await db.commit()

    asyncio.run(_seed_one_category())

    base = tmp_path / "KlinFiles"
    response = test_client.put(
        "/api/settings/default-base-path",
        json={"default_base_path": str(base)},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["default_base_path"] == str(base)
    assert body["updated_count"] == 1
    updated_paths = [c["folder_path"] for c in body["updated_categories"]]
    assert f"{base}/Inbox" in updated_paths


def test_onboarding_status_pending_by_default(test_client) -> None:
    response = test_client.get("/api/settings/onboarding")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending"
    assert body["onboarding_seeded"] is False
    assert body["should_seed_defaults"] is True
