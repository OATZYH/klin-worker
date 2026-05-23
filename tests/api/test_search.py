from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.search import search_files
from app.db.models import File, FileAnalysis
from app.models.request import FileSearchRequest
from app.services import search_service as search_module
from app.services.search_service import (
    _semantic_chunk_diagnostics,
    _semantic_payload_diagnostics,
)


class FakeDocStatus:
    def __init__(self, records: dict[str, dict] | None = None) -> None:
        self._data = records or {}


class FakeChunksVdb:
    def __init__(self, owner: "FakeLightRAG") -> None:
        self.owner = owner

    async def query(self, query: str, top_k: int) -> list[dict]:
        self.owner.calls.append((query, top_k))
        if self.owner.fail:
            raise RuntimeError("semantic unavailable")
        return self.owner.chunks[:top_k]


class FakeTextChunks:
    def __init__(self, chunks_by_id: dict[str, dict]) -> None:
        self.chunks_by_id = chunks_by_id

    async def get_by_id(self, chunk_id: str) -> dict | None:
        return self.chunks_by_id.get(chunk_id)


class FakeLightRAG:
    def __init__(
        self,
        references: list[str] | None = None,
        *,
        chunks: list[dict] | None = None,
        fail: bool = False,
        doc_status: FakeDocStatus | None = None,
    ) -> None:
        self.references = references or []
        self.chunks = chunks if chunks is not None else [
            {
                "id": f"chunk-{index}",
                "file_path": path,
                "content": f"semantic match for {path}",
                "distance": 0.9,
            }
            for index, path in enumerate(self.references)
        ]
        self.fail = fail
        self.calls: list[tuple[str, object]] = []
        self.doc_status = doc_status
        self.chunks_vdb = FakeChunksVdb(self)
        self.text_chunks = FakeTextChunks({chunk["id"]: chunk for chunk in self.chunks if "id" in chunk})


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
        chunks: list[dict] | None = None,
        ready: bool = True,
        fail: bool = False,
        doc_status_records: dict[str, dict] | None = None,
    ) -> None:
        self.is_ready = ready
        self.light = FakeLightRAG(
            references,
            chunks=chunks,
            fail=fail,
            doc_status=FakeDocStatus(doc_status_records),
        )
        self._rag = FakeRagEngine(self.light)


class FakeIngest:
    def __init__(self, pending_count: int = 0) -> None:
        self._pending_count = pending_count

    def pending_count(self) -> int:
        return self._pending_count


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
            response = await search_files(
                FileSearchRequest(query="   "),
                db=db,
                rag=rag,
                ingest=FakeIngest(),
            )
            return response

        response = await _with_db([], check)

        assert response.results == []
        assert rag.light.calls == []
        assert response.semantic_status == "ready"
        assert response.indexing_pending_count == 0

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
                ingest=FakeIngest(),
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
        assert response.semantic_status == "not_ready"
        assert response.semantic_error == "RAG service is not ready."

    asyncio.run(run())


def test_search_filename_matches_basename_not_folder() -> None:
    async def run() -> None:
        budget = _file(file_id="file-budget", path="/workspace/invoice/Budget.pdf")

        async def check(db: AsyncSession):
            return await search_files(
                FileSearchRequest(query="invoice"),
                db=db,
                rag=FakeRagService(ready=False),
                ingest=FakeIngest(),
            )

        response = await _with_db([budget], check)

        assert response.results == []
        assert response.semantic_status == "not_ready"

    asyncio.run(run())


def test_search_filename_matches_separator_normalized_basename() -> None:
    async def run() -> None:
        paper = _file(
            file_id="file-paper",
            path="/workspace/papers/DeepSeek_OCR_paper.pdf",
        )

        async def check(db: AsyncSession):
            return await search_files(
                FileSearchRequest(query="deepseek ocr"),
                db=db,
                rag=FakeRagService(ready=False),
                ingest=FakeIngest(),
            )

        response = await _with_db([paper], check)

        assert [item.id for item in response.results] == ["file-paper"]
        assert response.semantic_status == "not_ready"

    asyncio.run(run())


def test_search_deepseek_finds_pdf_by_filename() -> None:
    async def run() -> None:
        paper = _file(
            file_id="file-paper",
            path="/workspace/papers/DeepSeek_OCR_paper.pdf",
        )

        async def check(db: AsyncSession):
            return await search_files(
                FileSearchRequest(query="deepseek"),
                db=db,
                rag=FakeRagService(chunks=[]),
                ingest=FakeIngest(),
            )

        response = await _with_db([paper], check)

        assert [item.id for item in response.results] == ["file-paper"]
        assert response.semantic_status == "ready"

    asyncio.run(run())


def test_search_phrase_match_accepts_low_score_chunk(monkeypatch) -> None:
    async def run() -> None:
        monkeypatch.setattr(search_module.settings, "search_semantic_min_score", 0.8)
        paper = _file(
            file_id="file-paper",
            path="/workspace/papers/DeepSeek_OCR_paper.pdf",
        )

        async def check(db: AsyncSession):
            return await search_files(
                FileSearchRequest(query="ultra-high-resolution inputs"),
                db=db,
                rag=FakeRagService(
                    chunks=[
                        {
                            "id": "chunk-paper",
                            "file_path": "/workspace/papers/DeepSeek_OCR_paper.pdf",
                            "content": "The model handles ultra-high-resolution inputs efficiently.",
                            "distance": 0.1,
                        }
                    ]
                ),
                ingest=FakeIngest(),
            )

        response = await _with_db([paper], check)

        assert [item.id for item in response.results] == ["file-paper"]

    asyncio.run(run())


def test_search_singular_plural_token_match_accepts_llms(monkeypatch) -> None:
    async def run() -> None:
        monkeypatch.setattr(search_module.settings, "search_semantic_min_score", 0.8)
        paper = _file(
            file_id="file-paper",
            path="/workspace/papers/DeepSeek_OCR_paper.pdf",
        )

        async def check(db: AsyncSession):
            return await search_files(
                FileSearchRequest(query="llm"),
                db=db,
                rag=FakeRagService(
                    chunks=[
                        {
                            "id": "chunk-paper",
                            "file_path": "/workspace/papers/DeepSeek_OCR_paper.pdf",
                            "content": "This improves long-context behavior for LLMs.",
                            "distance": 0.1,
                        }
                    ]
                ),
                ingest=FakeIngest(),
            )

        response = await _with_db([paper], check)

        assert [item.id for item in response.results] == ["file-paper"]

    asyncio.run(run())


def test_search_cat_does_not_match_inside_other_words_when_score_is_low(monkeypatch) -> None:
    async def run() -> None:
        monkeypatch.setattr(search_module.settings, "search_semantic_min_score", 0.8)
        paper = _file(
            file_id="file-paper",
            path="/workspace/papers/DeepSeek_OCR_paper.pdf",
        )

        async def check(db: AsyncSession):
            return await search_files(
                FileSearchRequest(query="cat"),
                db=db,
                rag=FakeRagService(
                    chunks=[
                        {
                            "id": "chunk-paper",
                            "file_path": "/workspace/papers/DeepSeek_OCR_paper.pdf",
                            "content": "Compression capability and allocation strategy.",
                            "distance": 0.1,
                        }
                    ]
                ),
                ingest=FakeIngest(),
            )

        response = await _with_db([paper], check)

        assert response.results == []
        assert response.semantic_status == "ready"

    asyncio.run(run())


def test_semantic_chunk_diagnostics_reports_filtered_and_accepted(monkeypatch) -> None:
    async def run() -> None:
        monkeypatch.setattr(search_module.settings, "search_semantic_min_score", 0.8)
        lightrag = FakeLightRAG(
            chunks=[
                {
                    "id": "chunk-accepted",
                    "file_path": "/workspace/papers/DeepSeek_OCR_paper.pdf",
                    "content": "Ultra high resolution inputs",
                    "distance": 0.1,
                },
                {
                    "id": "chunk-filtered",
                    "file_path": "/workspace/papers/Other.pdf",
                    "content": "No matching terms",
                    "distance": 0.1,
                },
            ]
        )

        diagnostics = await _semantic_chunk_diagnostics(
            lightrag,
            lightrag.chunks,
            "ultra-high-resolution inputs",
        )

        assert diagnostics.paths == ["/workspace/papers/DeepSeek_OCR_paper.pdf"]
        assert diagnostics.chunk_count == 2
        assert diagnostics.accepted_chunk_count == 1
        assert diagnostics.filtered_chunk_count == 1
        assert diagnostics.lexical_match_count == 1
        assert diagnostics.score_match_count == 0

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
                ingest=FakeIngest(),
            )

        response = await _with_db([invoice, roadmap], check)

        assert [item.id for item in response.results] == ["file-invoice", "file-roadmap"]
        assert response.semantic_status == "ready"
        assert response.semantic_error is None

    asyncio.run(run())


def test_search_falls_back_to_filename_results_when_rag_fails() -> None:
    async def run() -> None:
        invoice = _file(file_id="file-invoice", path="/workspace/finance/Invoice.pdf")

        async def check(db: AsyncSession):
            return await search_files(
                FileSearchRequest(query="invoice"),
                db=db,
                rag=FakeRagService(fail=True),
                ingest=FakeIngest(),
            )

        response = await _with_db([invoice], check)

        assert [item.id for item in response.results] == ["file-invoice"]
        assert response.semantic_status == "degraded"
        assert response.semantic_error == "semantic unavailable"

    asyncio.run(run())


def test_search_skips_semantic_references_without_sqlite_rows() -> None:
    async def run() -> None:
        known = _file(file_id="file-known", path="/workspace/known/Notes.txt", extension=".txt")

        async def check(db: AsyncSession):
            return await search_files(
                FileSearchRequest(query="budget"),
                db=db,
                rag=FakeRagService(references=["/workspace/missing/Budget.pdf"]),
                ingest=FakeIngest(),
            )

        response = await _with_db([known], check)

        assert response.results == []
        assert response.semantic_status == "ready"

    asyncio.run(run())


def test_search_reports_pending_when_background_ingest_has_work() -> None:
    async def run() -> None:
        invoice = _file(file_id="file-invoice", path="/workspace/finance/Invoice.pdf")

        async def check(db: AsyncSession):
            return await search_files(
                FileSearchRequest(query="invoice"),
                db=db,
                rag=FakeRagService(),
                ingest=FakeIngest(pending_count=2),
            )

        response = await _with_db([invoice], check)

        assert [item.id for item in response.results] == ["file-invoice"]
        assert response.semantic_status == "pending"
        assert response.indexing_pending_count == 2

    asyncio.run(run())


def test_search_reports_degraded_when_lightrag_doc_status_failed() -> None:
    async def run() -> None:
        paper = _file(
            file_id="file-paper",
            path="/workspace/papers/DeepSeek_OCR_paper.pdf",
        )

        async def check(db: AsyncSession):
            return await search_files(
                FileSearchRequest(query="deepseek ocr"),
                db=db,
                rag=FakeRagService(
                    doc_status_records={
                        "doc-1": {
                            "file_path": "/workspace/papers/DeepSeek_OCR_paper.pdf",
                            "status": "failed",
                            "error_msg": "embedding 500",
                        }
                    }
                ),
                ingest=FakeIngest(),
            )

        response = await _with_db([paper], check)

        assert [item.id for item in response.results] == ["file-paper"]
        assert response.semantic_status == "degraded"
        assert response.semantic_error == "embedding 500"

    asyncio.run(run())


def test_search_reports_pending_when_lightrag_doc_status_processing() -> None:
    async def run() -> None:
        paper = _file(
            file_id="file-paper",
            path="/workspace/papers/DeepSeek_OCR_paper.pdf",
        )

        async def check(db: AsyncSession):
            return await search_files(
                FileSearchRequest(query="deepseek ocr"),
                db=db,
                rag=FakeRagService(
                    doc_status_records={
                        "doc-1": {
                            "file_path": "/workspace/papers/DeepSeek_OCR_paper.pdf",
                            "status": "processing",
                        }
                    }
                ),
                ingest=FakeIngest(),
            )

        response = await _with_db([paper], check)

        assert [item.id for item in response.results] == ["file-paper"]
        assert response.semantic_status == "pending"

    asyncio.run(run())


def test_semantic_payload_diagnostics_counts_references_chunks_and_invalid_paths() -> None:
    diagnostics = _semantic_payload_diagnostics(
        {
            "data": {
                "references": [
                    {"file_path": "/workspace/a.pdf"},
                    {"file_path": "/workspace/a.pdf"},
                    {"file_path": ""},
                    {"other": "missing"},
                    "malformed",
                ],
                "chunks": [
                    {"file_path": "/workspace/b.pdf"},
                    {"file_path": "/workspace/a.pdf"},
                    {},
                ],
            }
        }
    )

    assert diagnostics.paths == ["/workspace/a.pdf", "/workspace/b.pdf"]
    assert diagnostics.reference_count == 5
    assert diagnostics.chunk_count == 3
    assert diagnostics.invalid_file_path_count == 4
    assert diagnostics.payload_is_dict is True
    assert diagnostics.data_is_dict is True


def test_search_traces_semantic_paths_missing_from_sqlite(monkeypatch) -> None:
    trace_updates: list[dict] = []
    monkeypatch.setattr(
        search_module,
        "update_current_span",
        lambda **kwargs: trace_updates.append(kwargs),
    )

    async def run() -> None:
        known = _file(file_id="file-known", path="/workspace/known/Notes.txt", extension=".txt")

        async def check(db: AsyncSession):
            return await search_files(
                FileSearchRequest(query="budget"),
                db=db,
                rag=FakeRagService(
                    references=[
                        "/workspace/known/Notes.txt",
                        "/workspace/missing/Budget.pdf",
                    ]
                ),
                ingest=FakeIngest(),
            )

        response = await _with_db([known], check)

        assert [item.id for item in response.results] == ["file-known"]

    asyncio.run(run())

    final_search_output = next(
        update["output"]
        for update in reversed(trace_updates)
        if "output" in update
        and isinstance(update["output"], dict)
        and "semantic_missing_known_file_count" in update["output"]
    )
    assert final_search_output["semantic_raw_path_count"] == 2
    assert final_search_output["semantic_matched_known_file_count"] == 1
    assert final_search_output["semantic_missing_known_file_count"] == 1
    assert final_search_output["final_result_count"] == 1
    assert "query" not in final_search_output
    assert "/workspace/missing/Budget.pdf" not in str(final_search_output)


def test_search_trace_includes_query_and_step_spans(monkeypatch) -> None:
    trace_updates: list[dict[str, Any]] = []
    span_names: list[str] = []

    @contextmanager
    def fake_observation(**kwargs):
        span_names.append(kwargs["name"])
        yield None

    monkeypatch.setattr(
        search_module,
        "update_current_span",
        lambda **kwargs: trace_updates.append(kwargs),
    )
    monkeypatch.setattr(search_module, "start_as_current_observation", fake_observation)

    async def run() -> None:
        known = _file(file_id="file-known", path="/workspace/known/Notes.txt", extension=".txt")

        async def check(db: AsyncSession):
            return await search_files(
                FileSearchRequest(query="budget"),
                db=db,
                rag=FakeRagService(references=["/workspace/known/Notes.txt"]),
                ingest=FakeIngest(),
            )

        await _with_db([known], check)

    asyncio.run(run())

    assert span_names == [
        "search.normalize_query",
        "search.load_known_files",
        "search.filename_match",
        "search.semantic_chunk_filter",
        "search.map_semantic_paths",
        "search.finalize_results",
    ]

    search_input = next(
        update["input"]
        for update in trace_updates
        if isinstance(update.get("input"), dict)
        and update["input"].get("normalized_query") == "budget"
    )
    assert search_input["query"] == "budget"

    traced_outputs = [
        update["output"]
        for update in trace_updates
        if isinstance(update.get("output"), dict)
    ]
    assert any("filename_match_count" in output for output in traced_outputs)
    assert any("accepted_chunk_count" in output for output in traced_outputs)
    assert any("semantic_missing_known_file_count" in output for output in traced_outputs)
    assert "/workspace/known/Notes.txt" not in str(traced_outputs)
