"""Tests for ClassificationService scoring + embedding helpers."""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pytest

from app.core.ai_exceptions import AiCapabilityUnavailableError
from app.db.models import Category
from app.services.organize.classification_service import ClassificationService

pytestmark = pytest.mark.unit


# ── Pure math ────────────────────────────────────────────────────────────


def test_cosine_similarity_identical_vectors_returns_one() -> None:
    result = ClassificationService._cosine_similarity([1.0, 0.0, 0.0], [1.0, 0.0, 0.0])
    assert result == pytest.approx(1.0)


def test_cosine_similarity_orthogonal_vectors_returns_zero() -> None:
    result = ClassificationService._cosine_similarity([1.0, 0.0], [0.0, 1.0])
    assert result == pytest.approx(0.0)


def test_cosine_similarity_zero_norm_returns_zero() -> None:
    result = ClassificationService._cosine_similarity([0.0, 0.0], [1.0, 1.0])
    assert result == 0.0


# ── classify_with_embedding ──────────────────────────────────────────────


class _FakeRag:
    async def ensure_embedding_available(self) -> None:
        return None

    async def embed_texts(self, texts: list[str]) -> list[np.ndarray]:
        # Return a deterministic single-axis vector.
        return [np.array([1.0, 0.0, 0.0], dtype=np.float32)]


async def test_classify_with_embedding_skips_when_no_categories(
    async_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = ClassificationService(_FakeRag())
    scores = await svc.classify_with_embedding(
        file_id="file-1",
        file_embedding=[1.0, 0.0, 0.0],
        db=async_session,
    )
    assert scores == []


async def test_classify_with_embedding_ranks_and_truncates(
    async_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.db.models import File
    from app.core import config as config_module

    monkeypatch.setattr(config_module.settings, "classification_top_k", 2, raising=False)

    # Seed file row to satisfy CategoryScore.file_id FK
    file_row = File(
        id="f-classify",
        original_path="/tmp/x.pdf",
        current_path="/tmp/x.pdf",
        hash="h",
        size=1,
        extension=".pdf",
    )
    async_session.add(file_row)

    # Three categories with embeddings pointing in different directions
    cats = [
        Category(
            id="c-aligned",
            name="Aligned",
            description="d",
            embedding=json.dumps([1.0, 0.0, 0.0]),
        ),
        Category(
            id="c-mid",
            name="Mid",
            description="d",
            embedding=json.dumps([0.5, 0.5, 0.0]),
        ),
        Category(
            id="c-ortho",
            name="Ortho",
            description="d",
            embedding=json.dumps([0.0, 1.0, 0.0]),
        ),
    ]
    async_session.add_all(cats)
    await async_session.flush()

    svc = ClassificationService(_FakeRag())
    scores = await svc.classify_with_embedding(
        file_id="f-classify",
        file_embedding=[1.0, 0.0, 0.0],
        db=async_session,
    )

    # Truncated to top_k=2 and sorted desc
    assert [s["category_id"] for s in scores] == ["c-aligned", "c-mid"]
    assert scores[0]["score"] >= scores[1]["score"]


# ── generate_category_embedding ──────────────────────────────────────────


async def test_generate_category_embedding_returns_vector() -> None:
    svc = ClassificationService(_FakeRag())
    vec = await svc.generate_category_embedding("Finance documents")
    assert vec is not None
    assert vec == pytest.approx([1.0, 0.0, 0.0])


async def test_generate_category_embedding_propagates_capability_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BrokenRag(_FakeRag):
        async def ensure_embedding_available(self) -> None:
            raise AiCapabilityUnavailableError("embedding", "embed down")

    svc = ClassificationService(_BrokenRag())
    with pytest.raises(AiCapabilityUnavailableError):
        await svc.generate_category_embedding("Anything")


async def test_generate_category_embedding_returns_none_on_other_failure() -> None:
    class _NoisyRag(_FakeRag):
        async def embed_texts(self, texts: list[str]) -> list[np.ndarray]:
            raise RuntimeError("transient")

    svc = ClassificationService(_NoisyRag())
    result = await svc.generate_category_embedding("Anything")
    assert result is None


# ── _get_file_embedding text composition ─────────────────────────────────


async def test_get_file_embedding_uses_summary_when_provided() -> None:
    captured: dict[str, Any] = {}

    class _CapturingRag(_FakeRag):
        async def embed_texts(self, texts: list[str]) -> list[np.ndarray]:
            captured["texts"] = texts
            return [np.array([0.0, 1.0, 0.0], dtype=np.float32)]

    svc = ClassificationService(_CapturingRag())
    await svc.get_file_embedding("/tmp/report.pdf", summary="Quarterly results")
    assert captured["texts"] == ["report .pdf. Quarterly results"]


async def test_get_file_embedding_falls_back_to_filename_only() -> None:
    captured: dict[str, Any] = {}

    class _CapturingRag(_FakeRag):
        async def embed_texts(self, texts: list[str]) -> list[np.ndarray]:
            captured["texts"] = texts
            return [np.array([0.0, 1.0, 0.0], dtype=np.float32)]

    svc = ClassificationService(_CapturingRag())
    await svc.get_file_embedding("/tmp/report.pdf", summary=None)
    assert captured["texts"] == ["report .pdf"]
