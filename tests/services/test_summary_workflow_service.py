from __future__ import annotations

import asyncio
from typing import cast

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.organize.background_ingest import BackgroundIngestWorker
from app.services.summary.summary_service import SummaryService
from app.services.summary.summary_workflow_service import SummaryWorkflowService


class FakePreparedIngestResult:
    def __init__(self, ingest_status: str, extracted_text: str | None) -> None:
        self.ingest_status = ingest_status
        self.extracted_text = extracted_text


class FakeIngestWorker:
    def __init__(self, extracted_text: str | None) -> None:
        self._extracted_text = extracted_text
        self.calls: list[tuple[str, str | None, bool]] = []

    async def prepare(
        self,
        filepath: str,
        trace_id: str | None = None,
        *,
        enqueue_for_rag: bool = True,
    ) -> FakePreparedIngestResult:
        self.calls.append((filepath, trace_id, enqueue_for_rag))
        return FakePreparedIngestResult("queued", self._extracted_text)


class FakeSummaryService:
    def __init__(self, result: str | None) -> None:
        self._result = result
        self.calls: list[tuple[str, str | None]] = []

    async def summarise(self, file_path: str, extracted_text: str | None = None) -> str | None:
        self.calls.append((file_path, extracted_text))
        return self._result


def test_summary_workflow_uses_ingest_text_for_generation() -> None:
    async def run() -> None:
        ingest_worker = FakeIngestWorker("parsed text from fast docling")
        summary_service = FakeSummaryService(None)
        workflow = SummaryWorkflowService(
            cast(SummaryService, summary_service),
            cast(BackgroundIngestWorker, ingest_worker),
        )

        summary = await workflow._generate_summary(
            db=cast(AsyncSession, object()),
            file_path="/tmp/report.pdf",
        )

        assert summary is None
        assert ingest_worker.calls == [("/tmp/report.pdf", None, False)]
        assert summary_service.calls == [
            ("/tmp/report.pdf", "parsed text from fast docling")
        ]

    asyncio.run(run())