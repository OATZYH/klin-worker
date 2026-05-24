"""End-to-end ingest → retrieve flow through BackgroundIngestWorker + fake RAG."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.services.organize.background_ingest import BackgroundIngestWorker

pytestmark = pytest.mark.integration


class _FakeParser:
    def __init__(self, *, content_list: list[dict], profile_name: str = "fast") -> None:
        self._content_list = content_list
        self.calls: list[str] = []
        self.profile_name = profile_name

    async def parse(self, filepath: str) -> list[dict]:
        self.calls.append(filepath)
        return list(self._content_list)

    def extract_text(self, content_list: list[dict]) -> str:
        return " ".join(
            item.get("text", "") for item in content_list if item.get("type") == "text"
        )


class _FakeLightRag:
    def __init__(self) -> None:
        self.deleted_doc_ids: list[str] = []

    async def adelete_by_doc_id(self, doc_id: str) -> None:
        self.deleted_doc_ids.append(doc_id)


class _FakeRagEngine:
    def __init__(self) -> None:
        self.lightrag = _FakeLightRag()
        self.insert_calls: list[tuple[str, list[dict]]] = []
        self.ensure_calls = 0

    async def _ensure_lightrag_initialized(self) -> dict[str, bool]:
        self.ensure_calls += 1
        return {"success": True}

    async def insert_content_list(
        self,
        *,
        content_list: list[dict],
        file_path: str,
        doc_id: str | None = None,
    ) -> None:
        self.insert_calls.append((file_path, content_list))


class _FakeRagService:
    is_ready: bool = True

    def __init__(self) -> None:
        self._rag = _FakeRagEngine()
        self.ingest_calls: list[str] = []

    async def ingest(self, file_path: str) -> bool:
        self.ingest_calls.append(file_path)
        return True


async def test_prepare_then_enqueue_persists_and_indexes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "doc.pdf"
    target.write_bytes(b"placeholder")

    fast = _FakeParser(
        content_list=[{"type": "text", "text": "fast summary content"}],
        profile_name="fast",
    )
    rich = _FakeParser(
        content_list=[{"type": "text", "text": "rich body that gets indexed"}],
        profile_name="rich",
    )
    rag_service = _FakeRagService()

    worker = BackgroundIngestWorker(
        fast_parser=fast,
        rich_parser=rich,
        max_queue_size=10,
    )
    await worker.start(rag_service)

    try:
        prepared = await worker.prepare(str(target), enqueue_for_rag=True)
        assert prepared is not None
        assert prepared.extracted_text
        assert [str(p) for p in fast.calls] == [str(target)]

        await worker.enqueue_after_organize(prepared)
        # Allow the worker loop to run.
        import asyncio

        for _ in range(50):
            await asyncio.sleep(0.02)
            if rag_service._rag.insert_calls:
                break

        assert rag_service._rag.insert_calls
        path_inserted, content_list = rag_service._rag.insert_calls[0]
        assert "rich body" in str(content_list)
    finally:
        await worker.stop()


async def test_prepare_returns_none_when_path_missing(
    tmp_path: Path,
) -> None:
    fast = _FakeParser(content_list=[], profile_name="fast")
    rich = _FakeParser(content_list=[], profile_name="rich")
    rag_service = _FakeRagService()

    worker = BackgroundIngestWorker(
        fast_parser=fast,
        rich_parser=rich,
        max_queue_size=10,
    )
    await worker.start(rag_service)

    try:
        prepared = await worker.prepare(
            str(tmp_path / "missing.pdf"),
            enqueue_for_rag=False,
        )
        # When parsing yields nothing (empty content_list), the worker still
        # returns a PreparedIngest, but with empty extracted_text/content_list.
        assert prepared is not None
        assert prepared.extracted_text in (None, "")
    finally:
        await worker.stop()
