from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import File, FileAnalysis
from app.models.response import (
    GoogleCalendarDateTimeResponse,
    GoogleCalendarEventDraftResponse,
    ScheduleEventCandidate,
    ScheduleExtractionResponse,
)
from app.services.ai.llm_client import llm_client
from app.services.files.scan_result import ScanResult
from app.services.organize.organize_pipeline import get_analysis_fingerprint, process_single_file


async def _with_db(callback):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            return await callback(db)
    finally:
        await engine.dispose()


class FakeScanner:
    def __init__(self, *, sha256: str = "hash-new") -> None:
        self.sha256 = sha256

    async def scan(self, file_path: str) -> ScanResult:
        return ScanResult(
            original_path=file_path,
            file_name="report.pdf",
            extension=".pdf",
            size_bytes=123,
            sha256=self.sha256,
            exists=True,
        )


class FakeRag:
    is_ready = True

    def ensure_ready(self) -> None:
        return None


@dataclass
class FakePreparedIngest:
    ingest_status: str = "deferred"
    extracted_text: str | None = "Meeting agenda on 2026-05-10 at 14:00"
    content_list: list[dict[str, Any]] | None = None
    deferred_job: object | None = None


class FakeIngest:
    def __init__(self, order: list[str]) -> None:
        self.order = order

    async def prepare(
        self,
        filepath: str,
        trace_id: str | None = None,
        *,
        enqueue_for_rag: bool = True,
    ) -> FakePreparedIngest:
        self.order.append("ingest")
        return FakePreparedIngest(
            content_list=[
                {
                    "type": "text",
                    "text": "Meeting agenda on 2026-05-10 at 14:00",
                    "page_idx": 1,
                }
            ],
            deferred_job=object(),
        )

    async def enqueue_after_organize(self, prepared: FakePreparedIngest) -> str:
        self.order.append("enqueue_after_organize")
        return "queued"


class FakeSchedule:
    def __init__(self, order: list[str], *, fail: bool = False) -> None:
        self.order = order
        self.fail = fail
        self.calls = 0

    async def extract(self, **kwargs) -> ScheduleExtractionResponse:
        self.calls += 1
        self.order.append("schedule")
        if self.fail:
            raise RuntimeError("bad schedule")
        return ScheduleExtractionResponse(
            events=[
                ScheduleEventCandidate(
                    type="meeting",
                    confidence=0.8,
                    source_pages=[1],
                    source_text="Meeting agenda on 2026-05-10 at 14:00",
                    missing_fields=[],
                    google_event=GoogleCalendarEventDraftResponse(
                        summary="Meeting",
                        start=GoogleCalendarDateTimeResponse(
                            dateTime="2026-05-10T14:00:00+07:00",
                            timeZone="Asia/Bangkok",
                        ),
                        end=GoogleCalendarDateTimeResponse(
                            dateTime="2026-05-10T15:00:00+07:00",
                            timeZone="Asia/Bangkok",
                        ),
                    ),
                )
            ]
        )


class FakeSummary:
    def __init__(self, order: list[str]) -> None:
        self.order = order

    async def summarise(self, file_path: str, extracted_text: str | None = None) -> str:
        self.order.append("summary")
        return "summary"


class FakeRename:
    async def suggest_names(self, **kwargs) -> list[str]:
        return ["report_meeting.pdf"]


class FakeClassifier:
    async def get_file_embedding(self, *args, **kwargs) -> list[float]:
        return [0.1, 0.2]

    async def classify_with_embedding(self, **kwargs) -> list[dict]:
        return []


class FakeHistoryEntry:
    metadata_json: str | None = None


class FakeHistory:
    async def log(self, **kwargs) -> FakeHistoryEntry:
        return FakeHistoryEntry()


class FakeSystemLog:
    def __init__(self) -> None:
        self.logs: list[dict[str, Any]] = []

    async def log(self, **kwargs) -> None:
        self.logs.append(kwargs)


async def _with_ready_llm(callback):
    original_general = llm_client.ensure_general_available
    original_embedding = llm_client.ensure_embedding_available

    async def ready_general() -> None:
        return None

    async def ready_embedding(*args, **kwargs) -> None:
        return None

    llm_client.ensure_general_available = ready_general
    llm_client.ensure_embedding_available = ready_embedding
    try:
        return await callback()
    finally:
        llm_client.ensure_general_available = original_general
        llm_client.ensure_embedding_available = original_embedding


def test_full_organize_extracts_schedule_between_ingest_and_summary() -> None:
    async def run() -> None:
        async def check(db: AsyncSession):
            order: list[str] = []
            schedule = FakeSchedule(order)
            result = await process_single_file(
                filepath="/tmp/report.pdf",
                force=False,
                db=db,
                scanner=FakeScanner(),
                rag=FakeRag(),
                classifier=FakeClassifier(),
                summary_svc=FakeSummary(order),
                rename_svc=FakeRename(),
                schedule_svc=schedule,
                history_svc=FakeHistory(),
                system_log_svc=FakeSystemLog(),
                ingest=FakeIngest(order),
            )
            return result, order, schedule.calls

        result, order, schedule_calls = await _with_ready_llm(lambda: _with_db(check))

        assert order[:3] == ["ingest", "schedule", "summary"]
        assert schedule_calls == 1
        assert result.error is None
        assert result.schedule is not None
        assert result.schedule.events[0].google_event.summary == "Meeting"

    asyncio.run(run())


def test_schedule_extraction_failure_does_not_fail_organize() -> None:
    async def run() -> None:
        async def check(db: AsyncSession):
            order: list[str] = []
            system_log = FakeSystemLog()
            result = await process_single_file(
                filepath="/tmp/report.pdf",
                force=False,
                db=db,
                scanner=FakeScanner(),
                rag=FakeRag(),
                classifier=FakeClassifier(),
                summary_svc=FakeSummary(order),
                rename_svc=FakeRename(),
                schedule_svc=FakeSchedule(order, fail=True),
                history_svc=FakeHistory(),
                system_log_svc=system_log,
                ingest=FakeIngest(order),
            )
            return result, system_log.logs

        result, logs = await _with_ready_llm(lambda: _with_db(check))

        assert result.error is None
        assert result.suggested_names == ["report_meeting.pdf"]
        assert result.schedule is not None
        assert result.schedule.error == "bad schedule"
        assert any(log["event_type"] == "organize_schedule_extraction_failed" for log in logs)

    asyncio.run(run())


def test_cached_organize_does_not_recompute_schedule() -> None:
    async def run() -> None:
        async def check(db: AsyncSession):
            file_record = File(
                id="file-cached",
                original_path="/tmp/report.pdf",
                current_path="/tmp/report.pdf",
                hash="hash-same",
                size=123,
                extension=".pdf",
            )
            db.add(file_record)
            await db.flush()
            categories_hash = await get_analysis_fingerprint(db)
            db.add(
                FileAnalysis(
                    file_id=file_record.id,
                    summary="cached summary",
                    suggested_names='["cached_report.pdf"]',
                    categories_hash=categories_hash,
                )
            )
            await db.commit()

            order: list[str] = []
            schedule = FakeSchedule(order)
            result = await process_single_file(
                filepath="/tmp/report.pdf",
                force=False,
                db=db,
                scanner=FakeScanner(sha256="hash-same"),
                rag=FakeRag(),
                classifier=FakeClassifier(),
                summary_svc=FakeSummary(order),
                rename_svc=FakeRename(),
                schedule_svc=schedule,
                history_svc=FakeHistory(),
                system_log_svc=FakeSystemLog(),
                ingest=FakeIngest(order),
            )
            return result, order, schedule.calls

        result, order, schedule_calls = await _with_db(check)

        assert result.file_id == "file-cached"
        assert result.suggested_names == ["cached_report.pdf"]
        assert result.schedule is None
        assert order == []
        assert schedule_calls == 0

    asyncio.run(run())
