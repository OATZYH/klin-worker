"""Tests for shared AI capability error types and helpers."""

from __future__ import annotations

import pytest
from fastapi import HTTPException, status

from app.core.ai_exceptions import (
    AiCapabilityUnavailableError,
    format_ai_capability_errors,
    to_service_unavailable_http_exception,
)

pytestmark = pytest.mark.unit


def test_ai_capability_unavailable_error_preserves_fields() -> None:
    err = AiCapabilityUnavailableError(capability="embedding", detail="embed model offline")
    assert err.capability == "embedding"
    assert err.detail == "embed model offline"
    assert str(err) == "embed model offline"
    assert isinstance(err, RuntimeError)


def test_to_service_unavailable_http_exception() -> None:
    err = AiCapabilityUnavailableError(capability="chat", detail="llama-server unreachable")
    http_exc = to_service_unavailable_http_exception(err)
    assert isinstance(http_exc, HTTPException)
    assert http_exc.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert http_exc.detail == "llama-server unreachable"


def test_format_ai_capability_errors_empty_iterable_returns_default() -> None:
    assert (
        format_ai_capability_errors([])
        == "Required AI capabilities are unavailable."
    )


def test_format_ai_capability_errors_dedups_identical_errors() -> None:
    err = AiCapabilityUnavailableError(capability="chat", detail="chat down")
    formatted = format_ai_capability_errors([err, err])
    assert formatted.count("chat down") == 1
    assert "chat" in formatted


def test_format_ai_capability_errors_preserves_capability_order() -> None:
    errors = [
        AiCapabilityUnavailableError(capability="chat", detail="chat down"),
        AiCapabilityUnavailableError(capability="embedding", detail="embed down"),
        AiCapabilityUnavailableError(capability="chat", detail="chat down"),
    ]
    formatted = format_ai_capability_errors(errors)
    # capabilities reported in first-seen order, deduped
    assert "chat, embedding" in formatted
    assert formatted.count("chat down") == 1
    assert "embed down" in formatted
