"""Integration tests for /api/summary routes."""

from __future__ import annotations

import json

import pytest

from app.core.ai_exceptions import AiCapabilityUnavailableError
from app.services.summary.summary_workflow_service import SummaryWorkflowService

pytestmark = pytest.mark.integration


def _override_workflow(app, summary_text):
    """Override the summary workflow dependency to bypass real LLM."""
    from app.api.summary import _get_summary_workflow

    class _FakeWorkflow:
        async def summarise_file(self, *, file_path: str, db) -> str | None:
            return summary_text

    app.dependency_overrides[_get_summary_workflow] = lambda: _FakeWorkflow()


def _override_workflow_raising(app, exc: Exception):
    from app.api.summary import _get_summary_workflow

    class _BrokenWorkflow:
        async def summarise_file(self, *, file_path: str, db):
            raise exc

    app.dependency_overrides[_get_summary_workflow] = lambda: _BrokenWorkflow()


def test_summary_returns_generated_text(fastapi_app, test_client) -> None:
    _override_workflow(fastapi_app, "fresh summary text")
    try:
        response = test_client.post(
            "/api/summary",
            json={"file_path": "/tmp/doc.pdf"},
        )
    finally:
        fastapi_app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"] == "fresh summary text"
    assert payload["processing_time_ms"] >= 0


def test_summary_uses_fallback_text_when_workflow_returns_none(
    fastapi_app,
    test_client,
) -> None:
    _override_workflow(fastapi_app, None)
    try:
        response = test_client.post(
            "/api/summary",
            json={"file_path": "/tmp/doc.pdf"},
        )
    finally:
        fastapi_app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "No summary could be generated" in response.json()["summary"]


def test_summary_returns_503_on_ai_capability_unavailable(
    fastapi_app,
    test_client,
) -> None:
    _override_workflow_raising(
        fastapi_app,
        AiCapabilityUnavailableError("chat", "llama-server down"),
    )
    try:
        response = test_client.post(
            "/api/summary",
            json={"file_path": "/tmp/doc.pdf"},
        )
    finally:
        fastapi_app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["detail"] == "llama-server down"


def test_summary_rejects_empty_file_path(test_client) -> None:
    response = test_client.post("/api/summary", json={"file_path": ""})
    assert response.status_code == 422


def test_summary_stream_emits_chunk_and_done_events(fastapi_app, test_client) -> None:
    _override_workflow(fastapi_app, "streamed summary")
    try:
        with test_client.stream(
            "POST",
            "/api/summary/stream",
            json={"file_path": "/tmp/doc.pdf"},
        ) as response:
            assert response.status_code == 200
            body = "".join(response.iter_text())
    finally:
        fastapi_app.dependency_overrides.clear()

    assert "event: chunk" in body
    assert "event: done" in body
    # extract the chunk payload
    chunk_line = next(
        line for line in body.splitlines()
        if line.startswith("data: ") and "delta" in line
    )
    payload = json.loads(chunk_line[len("data: "):])
    assert payload["delta"] == "streamed summary"


def test_summary_stream_returns_503_on_capability_error(
    fastapi_app,
    test_client,
) -> None:
    _override_workflow_raising(
        fastapi_app,
        AiCapabilityUnavailableError("chat", "chat down"),
    )
    try:
        response = test_client.post(
            "/api/summary/stream",
            json={"file_path": "/tmp/doc.pdf"},
        )
    finally:
        fastapi_app.dependency_overrides.clear()

    assert response.status_code == 503
