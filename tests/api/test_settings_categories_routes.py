"""Integration tests for /api/settings/categories routes."""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.ai_exceptions import AiCapabilityUnavailableError
from app.services.organize.classification_service import ClassificationService

pytestmark = pytest.mark.integration


def _override_classifier(fastapi_app, *, vector=None, raise_capability=False) -> None:
    from app.api.settings.categories import _get_classifier

    class _Classifier:
        async def generate_category_embedding(self, text: str):
            if raise_capability:
                raise AiCapabilityUnavailableError("embedding", "embed down")
            return vector or [0.1, 0.2, 0.3]

    fastapi_app.dependency_overrides[_get_classifier] = lambda: _Classifier()


def test_list_categories_empty(test_client) -> None:
    response = test_client.get("/api/settings/categories")
    assert response.status_code == 200
    assert response.json() == []


def test_create_category_201(fastapi_app, test_client) -> None:
    _override_classifier(fastapi_app)
    try:
        response = test_client.post(
            "/api/settings/categories",
            json={
                "name": "Work",
                "description": "Project documents",
                "enabled": True,
                "color": "#3b82f6",
            },
        )
    finally:
        fastapi_app.dependency_overrides.clear()
    assert response.status_code == 201

    listing = test_client.get("/api/settings/categories")
    assert listing.status_code == 200
    assert any(c["name"] == "Work" for c in listing.json())


def test_create_category_409_on_duplicate(fastapi_app, test_client) -> None:
    _override_classifier(fastapi_app)
    try:
        first = test_client.post(
            "/api/settings/categories",
            json={"name": "Finance"},
        )
        assert first.status_code == 201
        second = test_client.post(
            "/api/settings/categories",
            json={"name": "Finance"},
        )
    finally:
        fastapi_app.dependency_overrides.clear()
    assert second.status_code == 409


def test_create_category_503_when_embedding_unavailable(
    fastapi_app,
    test_client,
) -> None:
    _override_classifier(fastapi_app, raise_capability=True)
    try:
        response = test_client.post(
            "/api/settings/categories",
            json={"name": "Travel"},
        )
    finally:
        fastapi_app.dependency_overrides.clear()
    assert response.status_code == 503


def test_get_category_404(test_client) -> None:
    response = test_client.get("/api/settings/categories/nonexistent")
    assert response.status_code == 404


def test_get_category_returns_existing(fastapi_app, test_client) -> None:
    _override_classifier(fastapi_app)
    try:
        create = test_client.post(
            "/api/settings/categories",
            json={"name": "Health"},
        )
        assert create.status_code == 201
        listing = test_client.get("/api/settings/categories").json()
        cat_id = next(c["id"] for c in listing if c["name"] == "Health")
        response = test_client.get(f"/api/settings/categories/{cat_id}")
    finally:
        fastapi_app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["name"] == "Health"


def test_patch_category_404(test_client) -> None:
    response = test_client.patch(
        "/api/settings/categories/nonexistent",
        json={"enabled": False},
    )
    assert response.status_code == 404


def test_patch_category_updates_without_reembed(fastapi_app, test_client) -> None:
    _override_classifier(fastapi_app)
    try:
        create = test_client.post("/api/settings/categories", json={"name": "Misc"})
        assert create.status_code == 201
        cat_id = next(
            c["id"] for c in test_client.get("/api/settings/categories").json()
            if c["name"] == "Misc"
        )
        response = test_client.patch(
            f"/api/settings/categories/{cat_id}",
            json={"enabled": False},
        )
    finally:
        fastapi_app.dependency_overrides.clear()
    assert response.status_code == 204


def test_delete_category_404(test_client) -> None:
    response = test_client.delete("/api/settings/categories/nonexistent")
    assert response.status_code == 404


def test_delete_category_204(fastapi_app, test_client) -> None:
    _override_classifier(fastapi_app)
    try:
        create = test_client.post(
            "/api/settings/categories",
            json={"name": "ToDelete"},
        )
        assert create.status_code == 201
        cat_id = next(
            c["id"] for c in test_client.get("/api/settings/categories").json()
            if c["name"] == "ToDelete"
        )
        response = test_client.delete(f"/api/settings/categories/{cat_id}")
    finally:
        fastapi_app.dependency_overrides.clear()
    assert response.status_code == 204


def test_batch_create_categories_skips_duplicates(fastapi_app, test_client) -> None:
    _override_classifier(fastapi_app)
    try:
        first = test_client.post(
            "/api/settings/categories/batch",
            json={"categories": [{"name": "A"}, {"name": "B"}]},
        )
        assert first.status_code == 201
        # Re-submit one duplicate and one new — should not 409, just skip.
        second = test_client.post(
            "/api/settings/categories/batch",
            json={"categories": [{"name": "A"}, {"name": "C"}]},
        )
        assert second.status_code == 201
        listing = test_client.get("/api/settings/categories").json()
    finally:
        fastapi_app.dependency_overrides.clear()
    names = {c["name"] for c in listing}
    assert {"A", "B", "C"}.issubset(names)


def test_batch_create_rejects_empty_list(test_client) -> None:
    response = test_client.post(
        "/api/settings/categories/batch",
        json={"categories": []},
    )
    assert response.status_code == 422


def test_list_categories_active_only_filter(fastapi_app, test_client) -> None:
    _override_classifier(fastapi_app)
    try:
        test_client.post(
            "/api/settings/categories",
            json={"name": "Active1", "enabled": True},
        )
        # Create then disable a second one
        test_client.post(
            "/api/settings/categories",
            json={"name": "Inactive1", "enabled": True},
        )
        inactive_id = next(
            c["id"] for c in test_client.get("/api/settings/categories").json()
            if c["name"] == "Inactive1"
        )
        test_client.patch(
            f"/api/settings/categories/{inactive_id}",
            json={"enabled": False},
        )

        active_only = test_client.get("/api/settings/categories").json()
        all_cats = test_client.get(
            "/api/settings/categories", params={"active_only": "false"}
        ).json()
    finally:
        fastapi_app.dependency_overrides.clear()

    active_names = {c["name"] for c in active_only}
    all_names = {c["name"] for c in all_cats}
    assert "Active1" in active_names
    assert "Inactive1" not in active_names
    assert "Inactive1" in all_names
