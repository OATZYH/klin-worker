from __future__ import annotations

import asyncio

from app.services.organize.background_ingest import BackgroundIngestWorker


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
    def __init__(self) -> None:
        self.insert_calls: list[tuple[list[dict], str]] = []

    async def insert_content_list(self, *, content_list: list[dict], file_path: str) -> None:
        self.insert_calls.append((content_list, file_path))


class FakeRagService:
    def __init__(self) -> None:
        self.is_ready = True
        self._rag = FakeRagEngine()
        self.ingest_calls: list[str] = []

    async def ingest(self, file_path: str) -> None:
        self.ingest_calls.append(file_path)


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
        assert fast_parser.parse_calls == ["/tmp/report.pdf"]
        assert rich_parser.parse_calls == ["/tmp/report.pdf"]
        assert rag_service._rag.insert_calls == [(rich_content, "/tmp/report.pdf")]
        assert rag_service.ingest_calls == []

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
        assert rag_service._rag.insert_calls == []
        assert rag_service.ingest_calls == ["/tmp/report.pdf"]

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
        assert fast_parser.parse_calls == ["/tmp/report.pdf"]
        assert rich_parser.parse_calls == []
        assert worker.queue_size == 0
        assert worker.is_pending("/tmp/report.pdf") is False

    asyncio.run(run())