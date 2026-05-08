from __future__ import annotations

import asyncio

from app.services.ai.rag_service import ensure_rag_doc_status_compatible


class FakeDocStatusStorage:
    def __init__(self, data: dict[str, dict]) -> None:
        self._data = data
        self._storage_lock = asyncio.Lock()
        self.upsert_calls: list[dict[str, dict]] = []

    async def upsert(self, data: dict[str, dict]) -> None:
        self.upsert_calls.append(data)
        self._data.update(data)


class FakeLightRAG:
    def __init__(self, doc_status: FakeDocStatusStorage) -> None:
        self.doc_status = doc_status


class FakeRagEngine:
    def __init__(self, doc_status: FakeDocStatusStorage) -> None:
        self.lightrag = FakeLightRAG(doc_status)
        self.ensure_calls = 0

    async def _ensure_lightrag_initialized(self) -> dict[str, bool]:
        self.ensure_calls += 1
        return {"success": True}


def test_ensure_rag_doc_status_compatible_drops_unknown_fields() -> None:
    async def run() -> None:
        doc_status = FakeDocStatusStorage(
            {
                "doc-1": {
                    "content_summary": "resume",
                    "content_length": 1200,
                    "file_path": "/tmp/resume.pdf",
                    "status": "processed",
                    "created_at": "2026-04-22T00:00:00+00:00",
                    "updated_at": "2026-04-22T00:00:00+00:00",
                    "multimodal_processed": True,
                }
            }
        )
        rag_engine = FakeRagEngine(doc_status)

        changed = await ensure_rag_doc_status_compatible(rag_engine)

        assert changed == 1
        assert rag_engine.ensure_calls == 1
        assert len(doc_status.upsert_calls) == 1
        sanitized = doc_status.upsert_calls[0]["doc-1"]
        assert "multimodal_processed" not in sanitized
        assert sanitized["file_path"] == "/tmp/resume.pdf"
        assert sanitized["metadata"] == {}
        assert sanitized["error_msg"] is None
        assert sanitized["chunks_list"] == []

    asyncio.run(run())


def test_ensure_rag_doc_status_compatible_noops_when_storage_is_clean() -> None:
    async def run() -> None:
        doc_status = FakeDocStatusStorage(
            {
                "doc-1": {
                    "content_summary": "resume",
                    "content_length": 1200,
                    "file_path": "/tmp/resume.pdf",
                    "status": "processed",
                    "created_at": "2026-04-22T00:00:00+00:00",
                    "updated_at": "2026-04-22T00:00:00+00:00",
                    "metadata": {},
                    "error_msg": None,
                    "chunks_list": [],
                }
            }
        )
        rag_engine = FakeRagEngine(doc_status)

        changed = await ensure_rag_doc_status_compatible(rag_engine)

        assert changed == 0
        assert rag_engine.ensure_calls == 1
        assert doc_status.upsert_calls == []

    asyncio.run(run())