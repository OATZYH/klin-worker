"""Tests for category seed service — default seeding and embedding back-fill."""

from __future__ import annotations

import json
from typing import Any

import pytest
from sqlmodel import select

from app.db.models import Category
from app.services.categories.seed_service import (
    DEFAULT_CATEGORIES,
    generate_missing_embeddings,
    seed_default_categories,
)

pytestmark = pytest.mark.unit


async def test_seed_default_categories_inserts_on_empty_db(async_session) -> None:
    inserted = await seed_default_categories(async_session)
    assert inserted == len(DEFAULT_CATEGORIES)

    result = await async_session.execute(select(Category))
    rows = list(result.scalars().all())
    assert len(rows) == len(DEFAULT_CATEGORIES)
    assert all(row.is_default for row in rows)


async def test_seed_default_categories_skips_when_defaults_present(async_session) -> None:
    first = await seed_default_categories(async_session)
    second = await seed_default_categories(async_session)
    assert first == len(DEFAULT_CATEGORIES)
    assert second == 0


async def test_seed_default_categories_seeds_only_missing_when_user_rows_exist(
    async_session,
) -> None:
    # User has manually created a single category — no defaults yet.
    user_cat = Category(
        name="User",
        description="custom",
        color="#abcdef",
        is_default=False,
    )
    async_session.add(user_cat)
    await async_session.flush()

    inserted = await seed_default_categories(async_session)
    # All defaults are inserted (their names differ from "User").
    assert inserted == len(DEFAULT_CATEGORIES)


async def test_seed_default_categories_does_not_duplicate_names(async_session) -> None:
    # Pre-seed one default by name.
    sample = DEFAULT_CATEGORIES[0]
    existing = Category(
        name=sample["name"],
        description="user override",
        color="#000000",
        is_default=False,
    )
    async_session.add(existing)
    await async_session.flush()

    inserted = await seed_default_categories(async_session)
    # Inserted = total defaults minus the colliding name (skipped).
    assert inserted == len(DEFAULT_CATEGORIES) - 1


# ── generate_missing_embeddings ──────────────────────────────────────────


class _StubClassifier:
    def __init__(self, vector: list[float] | None = None) -> None:
        self.vector = vector or [0.1, 0.2, 0.3]
        self.calls: list[str] = []

    async def generate_category_embedding(self, text: str) -> list[float] | None:
        self.calls.append(text)
        return self.vector


async def test_generate_missing_embeddings_inserts_for_active_categories(
    async_session,
) -> None:
    cat = Category(name="A", description="desc", color="#111111", is_active=True)
    async_session.add(cat)
    await async_session.flush()

    classifier = _StubClassifier()
    inserted = await generate_missing_embeddings(async_session, classifier)
    assert inserted == 1
    refreshed = await async_session.get(Category, cat.id)
    assert refreshed is not None
    assert json.loads(refreshed.embedding) == classifier.vector


async def test_generate_missing_embeddings_skips_already_embedded(async_session) -> None:
    cat = Category(
        name="A",
        description="desc",
        color="#111111",
        is_active=True,
        embedding=json.dumps([0.0]),
    )
    async_session.add(cat)
    await async_session.flush()

    classifier = _StubClassifier()
    inserted = await generate_missing_embeddings(async_session, classifier)
    assert inserted == 0
    assert classifier.calls == []


async def test_generate_missing_embeddings_skips_inactive_categories(async_session) -> None:
    cat = Category(name="A", description="d", color="#111111", is_active=False)
    async_session.add(cat)
    await async_session.flush()

    classifier = _StubClassifier()
    inserted = await generate_missing_embeddings(async_session, classifier)
    assert inserted == 0


async def test_generate_missing_embeddings_skips_when_classifier_returns_none(
    async_session,
) -> None:
    cat = Category(name="A", description="d", color="#111111", is_active=True)
    async_session.add(cat)
    await async_session.flush()

    class _NullClassifier:
        async def generate_category_embedding(self, text: str) -> Any:
            return None

    inserted = await generate_missing_embeddings(async_session, _NullClassifier())
    assert inserted == 0
    refreshed = await async_session.get(Category, cat.id)
    assert refreshed.embedding is None
