"""Smoke + degraded-state tests for the /health endpoint."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_health_returns_degraded_when_startup_checks_fail(test_client) -> None:
    """The neutered lifespan skips startup checks, so /health reports degraded."""
    response = test_client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "degraded"
    assert "FastAPI" in payload["services"]
    assert payload["services"]["FastAPI"]["ok"] is True
    assert payload["onboarding_status"] == "pending"
    assert payload["onboarding_seeded"] is False
