"""Additional unit tests for LlmClient helpers and error paths."""

from __future__ import annotations

import pytest

from app.core.ai_exceptions import AiCapabilityUnavailableError
from app.services.ai.llm_client import LlmClient

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "text, max_chars, expected",
    [
        ("hello", 100, "hello"),
        ("hello", 0, "hello"),
        ("hello", -5, "hello"),
        ("abcdef", 3, "abc"),
    ],
    ids=["under_limit", "zero_max", "negative_max", "over_limit"],
)
def test_truncate_text_value(text: str, max_chars: int, expected: str) -> None:
    assert LlmClient._truncate_text_value(text, max_chars=max_chars) == expected


@pytest.mark.parametrize(
    "text, max_tokens, expected",
    [
        ("alpha beta gamma delta epsilon", 3, "alpha beta gamma"),
        ("x" * 100, 5, "x" * 20),
        ("", 10, ""),
    ],
    ids=["word_budget", "long_single_word_char_cap", "empty"],
)
def test_truncate_embedding_text_value(text: str, max_tokens: int, expected: str) -> None:
    assert LlmClient._truncate_embedding_text_value(text, max_tokens=max_tokens) == expected


def test_apply_input_char_guard_trims_string_content(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import config as config_module

    monkeypatch.setattr(config_module.settings, "llm_input_max_chars", 5, raising=False)
    client = LlmClient()
    guarded = client._apply_input_char_guard(
        [{"role": "user", "content": "abcdefghi"}]
    )
    assert guarded[0]["content"] == "abcde"


def test_apply_input_char_guard_trims_text_parts_in_list_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core import config as config_module

    monkeypatch.setattr(config_module.settings, "llm_input_max_chars", 4, raising=False)
    client = LlmClient()
    guarded = client._apply_input_char_guard(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "abcdefg"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,xxx"}},
                ],
            }
        ]
    )
    parts = guarded[0]["content"]
    assert parts[0]["text"] == "abcd"
    # image_url should be untouched
    assert parts[1]["image_url"]["url"] == "data:image/png;base64,xxx"


def test_build_chat_request_body_includes_response_format_when_provided() -> None:
    client = LlmClient()
    body = client._build_chat_request_body(
        [{"role": "user", "content": "x"}],
        temperature=0.1,
        max_tokens=10,
        response_format={"type": "json_object"},
    )
    assert body["response_format"] == {"type": "json_object"}
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert "stream" not in body


def test_chat_template_kwargs_disables_thinking() -> None:
    assert LlmClient._chat_template_kwargs() == {"enable_thinking": False}


async def test_ensure_general_available_raises_when_client_missing() -> None:
    client = LlmClient()
    with pytest.raises(AiCapabilityUnavailableError):
        await client.ensure_general_available()
