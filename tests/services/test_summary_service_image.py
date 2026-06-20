"""Tests for SummaryService image-handling and AI capability propagation."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest

from app.core.ai_exceptions import AiCapabilityUnavailableError
from app.services.summary.summary_service import SummaryService

pytestmark = pytest.mark.unit


async def test_summarise_text_truncates_long_extracted_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def _fake_achat(*, messages, temperature, max_tokens):
        captured["prompt"] = messages[0]["content"]
        return "ok"

    from app.services.summary import summary_service as module

    monkeypatch.setattr(module.llm_client, "achat", _fake_achat)

    extracted = "x" * 8000
    svc = SummaryService()
    result = await svc.summarise("/tmp/a.pdf", extracted_text=extracted)
    assert result == "ok"
    # Prompt must not contain the entire 8000-char blob — it is truncated to 6000.
    assert captured["prompt"].count("x") == 6000


async def test_summarise_returns_none_on_unrelated_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(*_args, **_kwargs):
        raise RuntimeError("transient")

    from app.services.summary import summary_service as module

    monkeypatch.setattr(module.llm_client, "achat", _boom)

    svc = SummaryService()
    result = await svc.summarise(
        "/tmp/a.pdf",
        extracted_text="some short context " * 5,
    )
    assert result is None


async def test_summarise_propagates_capability_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _capability(*_args, **_kwargs):
        raise AiCapabilityUnavailableError("chat", "chat down")

    from app.services.summary import summary_service as module

    monkeypatch.setattr(module.llm_client, "achat", _capability)

    svc = SummaryService()
    with pytest.raises(AiCapabilityUnavailableError):
        await svc.summarise(
            "/tmp/a.pdf",
            extracted_text="long enough extracted body " * 5,
        )


async def test_summarise_image_uses_vision_when_supported(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    image_bytes = b"PNG-PLACEHOLDER"
    image = tmp_path / "shot.png"
    image.write_bytes(image_bytes)

    captured: dict[str, Any] = {}

    async def _fake_vision(messages, *, temperature, max_tokens):
        captured["messages"] = messages
        return "vision summary"

    from app.services.summary import summary_service as module

    # supports_vision is a property derived from is_ready + _vision_supported.
    monkeypatch.setattr(type(module.llm_client), "supports_vision", property(lambda _self: True))
    monkeypatch.setattr(module.llm_client, "achat_with_vision", _fake_vision)

    svc = SummaryService()
    result = await svc.summarise(str(image))
    assert result == "vision summary"
    parts = captured["messages"][0]["content"]
    image_part = next(p for p in parts if p["type"] == "image_url")
    expected_b64 = base64.b64encode(image_bytes).decode("utf-8")
    assert expected_b64 in image_part["image_url"]["url"]
    assert image_part["image_url"]["url"].startswith("data:image/png;base64,")


async def test_summarise_image_falls_back_to_filename_when_no_vision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    image = tmp_path / "icon.jpg"
    image.write_bytes(b"\xff\xd8\xff")

    captured: dict[str, Any] = {}

    async def _fake_chat(*, messages, temperature, max_tokens):
        captured["prompt"] = messages[0]["content"]
        return "filename-based description"

    from app.services.summary import summary_service as module

    monkeypatch.setattr(type(module.llm_client), "supports_vision", property(lambda _self: False))
    monkeypatch.setattr(module.llm_client, "achat", _fake_chat)

    svc = SummaryService()
    result = await svc.summarise(str(image))
    assert result == "filename-based description"
    assert "icon.jpg" in captured["prompt"]


def test_get_text_context_uses_filename_when_text_below_threshold() -> None:
    path = Path("/tmp/file.txt")
    ctx = SummaryService._get_text_context(path, "tiny")
    assert ctx == "Filename: file.txt, Extension: .txt"


def test_get_text_context_returns_extracted_text_when_long_enough() -> None:
    path = Path("/tmp/file.txt")
    long_text = "x" * 100
    ctx = SummaryService._get_text_context(path, long_text)
    assert ctx == long_text
