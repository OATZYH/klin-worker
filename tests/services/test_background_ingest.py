from __future__ import annotations

import asyncio
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import app.services.organize.background_ingest as background_ingest_module
from app.core.config import settings
from app.services.organize.background_ingest import BackgroundIngestWorker


def _resolved(path: str) -> str:
    return str(Path(path).resolve())


class FakeParser:
    def __init__(self, profile_name: str, content_list: list[dict]) -> None:
        self.profile_name = profile_name
        self._content_list = content_list
        self.parse_calls: list[str] = []

    async def parse(self, filepath: str) -> list[dict]:
        self.parse_calls.append(str(filepath))
        return list(self._content_list)

    def extract_text(self, content_list: list[dict]) -> str:
        parts = [item["text"] for item in content_list if item.get("type") == "text"]
        return "\n\n".join(parts)


class FakeRagEngine:
    def __init__(
        self,
        *,
        doc_status: Any = None,
        fail_insert: bool = False,
        insert_status: str = "processed",
    ) -> None:
        self.insert_calls: list[tuple[list[dict], str, str | None]] = []
        self.ensure_calls = 0
        self.lightrag = FakeLightRag(doc_status)
        self.fail_insert = fail_insert
        self.insert_status = insert_status

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
        if self.fail_insert:
            raise RuntimeError("embedding failed")
        self.insert_calls.append((content_list, file_path, doc_id))
        if self.lightrag.doc_status is not None and doc_id is not None:
            self.lightrag.doc_status._data[doc_id] = {
                "file_path": file_path,
                "status": self.insert_status,
                "chunks_count": 1 if self.insert_status == "processed" else 0,
                "error_msg": "embedding 500" if self.insert_status == "failed" else None,
            }


class FakeLightRag:
    def __init__(self, doc_status: Any = None) -> None:
        self.doc_status = doc_status
        self.deleted_doc_ids: list[str] = []

    async def adelete_by_doc_id(self, doc_id: str) -> None:
        self.deleted_doc_ids.append(doc_id)
        if self.doc_status is not None:
            self.doc_status._data.pop(doc_id, None)


class FakeRagService:
    def __init__(
        self,
        *,
        doc_status: Any = None,
        fail_insert: bool = False,
        insert_status: str = "processed",
    ) -> None:
        self.is_ready = True
        self._rag = FakeRagEngine(
            doc_status=doc_status,
            fail_insert=fail_insert,
            insert_status=insert_status,
        )
        self.ingest_calls: list[str] = []
        self.ingest_ok = True

    async def ingest(self, file_path: str) -> bool:
        self.ingest_calls.append(file_path)
        return self.ingest_ok


class FakeDocStatus:
    def __init__(self, record: dict[str, Any] | None) -> None:
        self.record = record
        self._data = {"doc-existing": record} if record is not None else {}

    async def get_doc_by_file_path(self, file_path: str) -> dict[str, Any] | None:
        for record in self._data.values():
            candidate = record.get("file_path")
            if candidate == file_path or str(candidate).split("/")[-1] == str(file_path).split("/")[-1]:
                return record
        return None


def test_enqueue_returns_fast_text_and_worker_uses_rich_parse() -> None:
    async def run() -> None:
        fast_content = [{"type": "text", "text": "fast summary text", "page_idx": 0}]
        rich_content = [{"type": "text", "text": "rich ingest text", "page_idx": 0}]
        fast_parser = FakeParser("fast", fast_content)
        rich_parser = FakeParser("rich", rich_content)
        rag_service = FakeRagService()
        worker = BackgroundIngestWorker(fast_parser=fast_parser, rich_parser=rich_parser)

        await worker.start(rag_service)
        prepared = await worker.enqueue("/tmp/report.pdf")
        await asyncio.wait_for(worker._queue.join(), timeout=1.0)
        await worker.stop()

        assert prepared.ingest_status == "queued"
        assert prepared.extracted_text == "fast summary text"
        assert prepared.content_list == fast_content
        assert fast_parser.parse_calls == ["/tmp/report.pdf"]
        assert rich_parser.parse_calls == ["/tmp/report.pdf"]
        assert rag_service._rag.insert_calls == [
            (rich_content, _resolved("/tmp/report.pdf"), worker._stable_doc_id("/tmp/report.pdf"))
        ]
        assert rag_service.ingest_calls == []
        state = worker.get_state("/tmp/report.pdf")
        assert state is not None
        assert state.status == "indexed"
        assert state.error is None

    asyncio.run(run())


def test_worker_falls_back_to_rag_ingest_when_rich_parse_is_empty() -> None:
    async def run() -> None:
        fast_content = [{"type": "text", "text": "fast summary text", "page_idx": 0}]
        fast_parser = FakeParser("fast", fast_content)
        rich_parser = FakeParser("rich", [])
        rag_service = FakeRagService()
        worker = BackgroundIngestWorker(fast_parser=fast_parser, rich_parser=rich_parser)

        await worker.start(rag_service)
        prepared = await worker.enqueue("/tmp/report.pdf")
        await asyncio.wait_for(worker._queue.join(), timeout=1.0)
        await worker.stop()

        assert prepared.extracted_text == "fast summary text"
        assert prepared.content_list == fast_content
        assert rag_service._rag.insert_calls == []
        assert rag_service.ingest_calls == ["/tmp/report.pdf"]
        assert worker.get_state("/tmp/report.pdf").status == "indexed"  # type: ignore[union-attr]

    asyncio.run(run())


def test_prepare_returns_text_without_queue_when_worker_not_ready() -> None:
    async def run() -> None:
        fast_content = [{"type": "text", "text": "fast summary text", "page_idx": 0}]
        fast_parser = FakeParser("fast", fast_content)
        rich_parser = FakeParser("rich", [])
        worker = BackgroundIngestWorker(fast_parser=fast_parser, rich_parser=rich_parser)

        prepared = await worker.prepare("/tmp/report.pdf")

        assert prepared.ingest_status == "not_ready"
        assert prepared.extracted_text == "fast summary text"
        assert prepared.content_list == fast_content
        assert fast_parser.parse_calls == ["/tmp/report.pdf"]
        assert rich_parser.parse_calls == []
        assert worker.queue_size == 0
        assert worker.is_pending("/tmp/report.pdf") is False
        assert worker.get_state("/tmp/report.pdf").status == "not_ready"  # type: ignore[union-attr]

    asyncio.run(run())


def test_worker_marks_failed_when_rag_insert_raises() -> None:
    async def run() -> None:
        fast_content = [{"type": "text", "text": "fast summary text", "page_idx": 0}]
        rich_content = [{"type": "text", "text": "rich ingest text", "page_idx": 0}]
        worker = BackgroundIngestWorker(
            fast_parser=FakeParser("fast", fast_content),
            rich_parser=FakeParser("rich", rich_content),
        )
        rag_service = FakeRagService(fail_insert=True)

        await worker.start(rag_service)
        await worker.enqueue("/tmp/report.pdf")
        await asyncio.wait_for(worker._queue.join(), timeout=1.0)
        await worker.stop()

        state = worker.get_state("/tmp/report.pdf")
        assert state is not None
        assert state.status == "failed"
        assert state.error == "embedding failed"

    asyncio.run(run())


def test_worker_marks_failed_when_lightrag_doc_status_failed() -> None:
    async def run() -> None:
        fast_content = [{"type": "text", "text": "fast summary text", "page_idx": 0}]
        rich_content = [{"type": "text", "text": "rich ingest text", "page_idx": 0}]
        doc_status = FakeDocStatus(
            {
                "file_path": "/tmp/report.pdf",
                "status": "failed",
                "chunks_count": 0,
                "error_msg": "embedding 500",
            }
        )
        worker = BackgroundIngestWorker(
            fast_parser=FakeParser("fast", fast_content),
            rich_parser=FakeParser("rich", rich_content),
        )
        rag_service = FakeRagService(doc_status=doc_status, insert_status="failed")

        await worker.start(rag_service)
        await worker.enqueue("/tmp/report.pdf")
        await asyncio.wait_for(worker._queue.join(), timeout=1.0)
        await worker.stop()

        state = worker.get_state("/tmp/report.pdf")
        assert state is not None
        assert state.status == "failed"
        assert state.error == "embedding 500"

    asyncio.run(run())


def test_worker_deletes_existing_rag_docs_before_reinsert() -> None:
    async def run() -> None:
        doc_status = FakeDocStatus(
            {
                "file_path": "/old/location/report.pdf",
                "status": "processed",
                "chunks_count": 1,
            }
        )
        worker = BackgroundIngestWorker(
            fast_parser=FakeParser("fast", []),
            rich_parser=FakeParser("rich", []),
        )
        rag_service = FakeRagService(doc_status=doc_status)
        worker._rag = rag_service

        await worker._ingest_to_rag(
            [{"type": "text", "text": "new searchable text", "page_idx": 0}],
            "/tmp/report.pdf",
        )

        assert rag_service._rag.lightrag.deleted_doc_ids == ["doc-existing"]
        assert rag_service._rag.insert_calls == [
                (
                    [{"type": "text", "text": "new searchable text", "page_idx": 0}],
                    _resolved("/tmp/report.pdf"),
                    worker._stable_doc_id("/tmp/report.pdf"),
                )
            ]

    asyncio.run(run())


def test_worker_installs_kg_skip_hook_when_disabled(monkeypatch) -> None:
    async def run() -> None:
        monkeypatch.setattr(settings, "rag_enable_kg_extraction", False)
        worker = BackgroundIngestWorker(
            fast_parser=FakeParser("fast", []),
            rich_parser=FakeParser("rich", []),
        )
        rag_service = FakeRagService()
        worker._rag = rag_service

        await worker._ingest_to_rag(
            [{"type": "text", "text": "searchable text", "page_idx": 0}],
            "/tmp/report.pdf",
        )

        lightrag = rag_service._rag.lightrag
        assert lightrag._klin_kg_extraction_disabled is True
        assert await lightrag._process_extract_entities({}) == []

    asyncio.run(run())


def test_worker_converts_disabled_table_blocks_to_text(monkeypatch) -> None:
    async def run() -> None:
        monkeypatch.setattr(settings, "rag_enable_table_processing", False)
        worker = BackgroundIngestWorker(
            fast_parser=FakeParser("fast", []),
            rich_parser=FakeParser("rich", []),
        )
        rag_service = FakeRagService()
        worker._rag = rag_service

        await worker._ingest_to_rag(
            [
                {
                    "type": "table",
                    "table_caption": ["Model scores"],
                    "table_body": "| model | score |",
                    "table_footnote": ["higher is better"],
                    "page_idx": 3,
                }
            ],
            "/tmp/report.pdf",
        )

        assert rag_service._rag.insert_calls == [
            (
                [
                    {
                        "type": "text",
                        "text": "Model scores\n\n| model | score |\n\nhigher is better",
                        "page_idx": 3,
                    }
                ],
                _resolved("/tmp/report.pdf"),
                worker._stable_doc_id("/tmp/report.pdf"),
            )
        ]

    asyncio.run(run())


def test_worker_converts_disabled_equation_blocks_to_text(monkeypatch) -> None:
    async def run() -> None:
        monkeypatch.setattr(settings, "rag_enable_equation_processing", False)
        worker = BackgroundIngestWorker(
            fast_parser=FakeParser("fast", []),
            rich_parser=FakeParser("rich", []),
        )
        rag_service = FakeRagService()
        worker._rag = rag_service

        await worker._ingest_to_rag(
            [
                {
                    "type": "equation",
                    "text": "Energy mass equivalence",
                    "latex": "E = mc^2",
                    "page_idx": 4,
                }
            ],
            "/tmp/report.pdf",
        )

        assert rag_service._rag.insert_calls == [
            (
                [
                    {
                        "type": "text",
                        "text": "Energy mass equivalence\n\nE = mc^2",
                        "page_idx": 4,
                    }
                ],
                _resolved("/tmp/report.pdf"),
                worker._stable_doc_id("/tmp/report.pdf"),
            )
        ]

    asyncio.run(run())


def test_worker_retains_image_blocks_only_when_enabled(monkeypatch) -> None:
    async def run() -> None:
        worker = BackgroundIngestWorker(
            fast_parser=FakeParser("fast", []),
            rich_parser=FakeParser("rich", []),
        )
        rag_service = FakeRagService()
        worker._rag = rag_service
        image_item = {"type": "image", "img_path": "/tmp/page.png", "page_idx": 1}

        monkeypatch.setattr(settings, "rag_enable_image_processing", True)
        await worker._ingest_to_rag([image_item], "/tmp/image.png")

        monkeypatch.setattr(settings, "rag_enable_image_processing", False)
        await worker._ingest_to_rag(
            [
                image_item,
                {"type": "text", "text": "fallback searchable text", "page_idx": 1},
            ],
            "/tmp/report.pdf",
        )

        assert rag_service._rag.insert_calls == [
            (
                [
                    {"type": "text", "text": "Image file: image.png", "page_idx": 0},
                    image_item,
                ],
                _resolved("/tmp/image.png"),
                worker._stable_doc_id("/tmp/image.png"),
            ),
            (
                [{"type": "text", "text": "fallback searchable text", "page_idx": 1}],
                _resolved("/tmp/report.pdf"),
                worker._stable_doc_id("/tmp/report.pdf"),
            ),
        ]

    asyncio.run(run())


def test_background_trace_keeps_source_trace_as_metadata_not_trace_context() -> None:
    async def run() -> None:
        captured: list[dict[str, Any]] = []

        @contextmanager
        def fake_observation(**kwargs):
            captured.append(kwargs)
            yield None

        original = background_ingest_module.start_as_current_observation
        background_ingest_module.start_as_current_observation = fake_observation
        try:
            fast_content = [{"type": "text", "text": "fast summary text", "page_idx": 0}]
            rich_content = [{"type": "text", "text": "rich ingest text", "page_idx": 0}]
            worker = BackgroundIngestWorker(
                fast_parser=FakeParser("fast", fast_content),
                rich_parser=FakeParser("rich", rich_content),
            )

            await worker.start(FakeRagService())
            await worker.enqueue("/tmp/report.pdf", trace_id="request-trace")
            await asyncio.wait_for(worker._queue.join(), timeout=1.0)
            await worker.stop()
        finally:
            background_ingest_module.start_as_current_observation = original

        assert captured
        assert "trace_context" not in captured[0]
        assert captured[0]["metadata"]["source_trace_id"] == "request-trace"

    asyncio.run(run())
