from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.search import search_files
from app.db.models import File, FileAnalysis
from app.models.request import FileSearchRequest


class FakeLightRAG:
    def __init__(self, references: list[str] | None = None, *, fail: bool = False) -> None:
        self.references = references or []
        self.fail = fail
        self.calls: list[tuple[str, object]] = []

    async def aquery_llm(self, query: str, param: object | None = None) -> dict:
        self.calls.append((query, param))
        if self.fail:
            raise RuntimeError("semantic unavailable")
        return {
            "data": {
                "references": [
                    {"reference_id": str(index + 1), "file_path": path}
                    for index, path in enumerate(self.references)
                ],
            },
        }


class FakeRagEngine:
    def __init__(self, lightrag: FakeLightRAG) -> None:
        self.lightrag = lightrag

    async def _ensure_lightrag_initialized(self) -> dict[str, bool]:
        return {"success": True}


class FakeRagService:
    def __init__(
        self,
        references: list[str] | None = None,
        *,
        ready: bool = True,
        fail: bool = False,
    ) -> None:
        self.is_ready = ready
        self.light = FakeLightRAG(references, fail=fail)
        self._rag = FakeRagEngine(self.light)


async def _with_db(seed: list[File | FileAnalysis], callback):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            for row in seed:
                db.add(row)
            await db.commit()
            return await callback(db)
    finally:
        await engine.dispose()


def _file(
    *,
    file_id: str,
    path: str,
    original_path: str | None = None,
    size: int = 10,
    extension: str = ".pdf",
) -> File:
    return File(
        id=file_id,
        original_path=original_path or path,
        current_path=path,
        hash=f"hash-{file_id}",
        size=size,
        extension=extension,
        created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )


def test_search_whitespace_query_returns_empty_results() -> None:
    async def run() -> None:
        rag = FakeRagService()

        async def check(db: AsyncSession):
            response = await search_files(FileSearchRequest(query="   "), db=db, rag=rag)
            return response

        response = await _with_db([], check)

        assert response.results == []
        assert rag.light.calls == []

    asyncio.run(run())


def test_search_filename_returns_sqlite_rows_without_mock_data() -> None:
    async def run() -> None:
        invoice = _file(
            file_id="file-invoice",
            path="/workspace/finance/Invoice-2026-02.pdf",
            size=245_760,
        )
        unrelated = _file(
            file_id="file-notes",
            path="/workspace/work/Project-Alpha-Meeting-Notes.txt",
            extension=".txt",
        )
        analysis = FileAnalysis(
            file_id=invoice.id,
            processed_at=datetime(2026, 5, 2, tzinfo=timezone.utc),
        )

        async def check(db: AsyncSession):
            return await search_files(
                FileSearchRequest(query="invoice"),
                db=db,
                rag=FakeRagService(ready=False),
            )

        response = await _with_db([invoice, unrelated, analysis], check)

        assert [item.id for item in response.results] == ["file-invoice"]
        item = response.results[0]
        assert item.file_name == "Invoice-2026-02.pdf"
        assert item.file_type == "pdf"
        assert item.size_bytes == 245_760
        assert item.folder == "/workspace/finance"
        assert item.path == "/workspace/finance/Invoice-2026-02.pdf"
        assert item.last_edited == datetime(2026, 5, 2, tzinfo=timezone.utc)

    asyncio.run(run())


def test_search_filename_matches_basename_not_folder() -> None:
    async def run() -> None:
        budget = _file(file_id="file-budget", path="/workspace/invoice/Budget.pdf")

        async def check(db: AsyncSession):
            return await search_files(
                FileSearchRequest(query="invoice"),
                db=db,
                rag=FakeRagService(ready=False),
            )

        response = await _with_db([budget], check)

        assert response.results == []

    asyncio.run(run())


def test_search_appends_semantic_references_after_filename_matches_and_dedupes() -> None:
    async def run() -> None:
        invoice = _file(file_id="file-invoice", path="/workspace/finance/Invoice.pdf")
        roadmap = _file(file_id="file-roadmap", path="/workspace/work/Roadmap.docx", extension=".docx")

        async def check(db: AsyncSession):
            return await search_files(
                FileSearchRequest(query="invoice"),
                db=db,
                rag=FakeRagService(
                    references=[
                        "/workspace/finance/Invoice.pdf",
                        "/workspace/work/Roadmap.docx",
                    ],
                ),
            )

        response = await _with_db([invoice, roadmap], check)

        assert [item.id for item in response.results] == ["file-invoice", "file-roadmap"]

    asyncio.run(run())


def test_search_falls_back_to_filename_results_when_rag_fails() -> None:
    async def run() -> None:
        invoice = _file(file_id="file-invoice", path="/workspace/finance/Invoice.pdf")

        async def check(db: AsyncSession):
            return await search_files(
                FileSearchRequest(query="invoice"),
                db=db,
                rag=FakeRagService(fail=True),
            )

        response = await _with_db([invoice], check)

        assert [item.id for item in response.results] == ["file-invoice"]

    asyncio.run(run())


def test_search_skips_semantic_references_without_sqlite_rows() -> None:
    async def run() -> None:
        known = _file(file_id="file-known", path="/workspace/known/Notes.txt", extension=".txt")

        async def check(db: AsyncSession):
            return await search_files(
                FileSearchRequest(query="budget"),
                db=db,
                rag=FakeRagService(references=["/workspace/missing/Budget.pdf"]),
            )

        response = await _with_db([known], check)

        assert response.results == []

    asyncio.run(run())
