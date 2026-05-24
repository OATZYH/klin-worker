"""Tests for startup_checks — diagnostic checks and error classification."""

from __future__ import annotations

import pytest

from app.core.ai_exceptions import AiCapabilityUnavailableError
from app.services.startup_checks import (
    CheckResult,
    _classify_db_error,
    check_database,
    check_llm_server,
    check_rag,
    run_all_checks,
)

pytestmark = pytest.mark.unit


# ── _classify_db_error ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected_prefix",
    [
        ("no such column 'foo'", "schema_mismatch"),
        ("no such table 'cats'", "schema_mismatch"),
        ("unable to open database file", "connection_failed"),
        ("database is locked", "database_locked"),
        ("attempt to write a readonly database", "permission_denied"),
        ("permission denied", "permission_denied"),
        ("database disk image is malformed", "database_corrupt"),
        ("the disk is full", "disk_full"),
        ("totally unknown error", "unexpected_error"),
    ],
)
def test_classify_db_error_maps_known_substrings(raw: str, expected_prefix: str) -> None:
    msg = _classify_db_error(RuntimeError(raw))
    assert msg.startswith(expected_prefix)


# ── check_database ───────────────────────────────────────────────────────


async def test_check_database_ok_on_clean_schema(async_session) -> None:
    result = await check_database(async_session)
    assert isinstance(result, CheckResult)
    assert result.ok is True
    assert "Database" in result.name


async def test_check_database_failure_returns_classified_detail() -> None:
    class _BrokenDb:
        async def execute(self, *_args, **_kwargs):
            raise RuntimeError("no such table foo")

    result = await check_database(_BrokenDb())  # type: ignore[arg-type]
    assert result.ok is False
    assert result.detail.startswith("schema_mismatch")


# ── check_llm_server ─────────────────────────────────────────────────────


async def test_check_llm_server_ok_when_chat_and_embed_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import startup_checks as module

    async def _ok(*_args, **_kwargs):
        return None

    monkeypatch.setattr(module.llm_client, "ensure_general_available", _ok)
    monkeypatch.setattr(module.llm_client, "ensure_embedding_available", _ok)

    result = await check_llm_server()
    assert result.ok is True
    assert "Chat connected" in result.detail
    assert "Embedding connected" in result.detail


async def test_check_llm_server_returns_fail_when_chat_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import startup_checks as module

    async def _fail(*_args, **_kwargs):
        raise AiCapabilityUnavailableError("chat", "chat down")

    monkeypatch.setattr(module.llm_client, "ensure_general_available", _fail)

    result = await check_llm_server()
    assert result.ok is False
    assert result.detail == "chat down"


async def test_check_llm_server_ok_but_notes_embedding_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import startup_checks as module

    async def _ok(*_args, **_kwargs):
        return None

    async def _fail_embed(*_args, **_kwargs):
        raise AiCapabilityUnavailableError("embedding", "embed down")

    monkeypatch.setattr(module.llm_client, "ensure_general_available", _ok)
    monkeypatch.setattr(module.llm_client, "ensure_embedding_available", _fail_embed)

    result = await check_llm_server()
    assert result.ok is True
    assert "Embedding unavailable" in result.detail


# ── check_rag ────────────────────────────────────────────────────────────


class _FakeRag:
    is_ready: bool = True

    def ensure_ready(self) -> None:
        return None


async def test_check_rag_returns_ok_when_ready() -> None:
    result = await check_rag(_FakeRag())
    assert result.ok is True


async def test_check_rag_returns_fail_when_not_ready() -> None:
    rag = _FakeRag()
    rag.is_ready = False
    result = await check_rag(rag)
    assert result.ok is False
    assert "Not initialised" in result.detail


async def test_check_rag_propagates_capability_detail() -> None:
    class _Broken:
        is_ready = False

        def ensure_ready(self) -> None:
            raise AiCapabilityUnavailableError("rag", "rag down")

    result = await check_rag(_Broken())
    assert result.ok is False
    assert result.detail == "rag down"


# ── run_all_checks ───────────────────────────────────────────────────────


async def test_run_all_checks_aggregates_three_results(
    async_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import startup_checks as module

    async def _ok(*_args, **_kwargs):
        return None

    monkeypatch.setattr(module.llm_client, "ensure_general_available", _ok)
    monkeypatch.setattr(module.llm_client, "ensure_embedding_available", _ok)

    results = await run_all_checks(async_session, _FakeRag())
    assert len(results) == 3
    assert all(isinstance(r, CheckResult) for r in results)
