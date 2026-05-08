"""
Search API router.

Hybrid file search over files known to the organize pipeline:
filename matches from SQLite first, semantic matches from LightRAG second.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import File, FileAnalysis
from app.db.session import get_db
from app.models.request import FileSearchRequest
from app.models.response import FileSearchResponse, FileSearchResultItem
from app.services.ai.rag_service import RagService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/search", tags=["search"])

DEFAULT_SEARCH_LIMIT = 20


def _get_rag() -> RagService:
    from app.main import get_rag_service

    return get_rag_service()


def _file_name(path: str) -> str:
    return Path(path).name


def _folder(path: str) -> str:
    return str(Path(path).parent)


def _file_type(file_record: File) -> str:
    extension = file_record.extension or Path(file_record.current_path).suffix
    return extension.lstrip(".").lower()


def _last_edited(file_record: File, analysis: FileAnalysis | None) -> datetime:
    try:
        mtime = Path(file_record.current_path).stat().st_mtime
        return datetime.fromtimestamp(mtime, tz=timezone.utc)
    except OSError:
        return analysis.processed_at if analysis else file_record.created_at


def _to_search_item(file_record: File, analysis: FileAnalysis | None) -> FileSearchResultItem:
    return FileSearchResultItem(
        id=file_record.id,
        file_name=_file_name(file_record.current_path),
        file_type=_file_type(file_record),
        size_bytes=file_record.size,
        folder=_folder(file_record.current_path),
        last_edited=_last_edited(file_record, analysis),
        path=file_record.current_path,
    )


async def _load_known_files(db: AsyncSession) -> list[tuple[File, FileAnalysis | None]]:
    stmt = select(File, FileAnalysis).join(
        FileAnalysis,
        FileAnalysis.file_id == File.id,  # type: ignore[union-attr]
        isouter=True,
    )
    result = await db.exec(stmt)
    return [(file_record, analysis) for file_record, analysis in result.all()]


def _filename_matches(
    rows: list[tuple[File, FileAnalysis | None]],
    query: str,
) -> list[tuple[File, FileAnalysis | None]]:
    return [
        row
        for row in rows
        if query in _file_name(row[0].current_path).lower()
    ]


def _semantic_paths_from_payload(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []

    data = payload.get("data")
    if not isinstance(data, dict):
        return []

    ordered_paths: list[str] = []
    seen: set[str] = set()

    references = data.get("references")
    if isinstance(references, list):
        for reference in references:
            if not isinstance(reference, dict):
                continue
            file_path = reference.get("file_path")
            if isinstance(file_path, str) and file_path and file_path not in seen:
                ordered_paths.append(file_path)
                seen.add(file_path)

    chunks = data.get("chunks")
    if isinstance(chunks, list):
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            file_path = chunk.get("file_path")
            if isinstance(file_path, str) and file_path and file_path not in seen:
                ordered_paths.append(file_path)
                seen.add(file_path)

    return ordered_paths


async def _semantic_file_paths(
    rag: RagService,
    query: str,
    *,
    limit: int = DEFAULT_SEARCH_LIMIT,
) -> list[str]:
    if not getattr(rag, "is_ready", False):
        return []

    try:
        rag_engine = getattr(rag, "_rag", None)
        if rag_engine is None:
            return []

        if hasattr(rag_engine, "_ensure_lightrag_initialized"):
            init_result = await rag_engine._ensure_lightrag_initialized()  # noqa: SLF001
            if isinstance(init_result, dict) and not init_result.get("success", True):
                return []

        lightrag = getattr(rag_engine, "lightrag", None)
        if lightrag is None or not hasattr(lightrag, "aquery_llm"):
            return []

        from lightrag import QueryParam

        payload = await lightrag.aquery_llm(
            query,
            param=QueryParam(
                mode="naive",
                only_need_context=True,
                top_k=limit,
                chunk_top_k=limit,
                enable_rerank=False,
            ),
        )
        return _semantic_paths_from_payload(payload)
    except Exception as exc:
        logger.warning("Semantic search failed; returning filename matches only: %s", exc)
        return []


def _append_unique(
    results: list[tuple[File, FileAnalysis | None]],
    row: tuple[File, FileAnalysis | None] | None,
    seen_ids: set[str],
) -> None:
    if row is None:
        return
    file_id = row[0].id
    if file_id in seen_ids:
        return
    results.append(row)
    seen_ids.add(file_id)


async def _search_known_files(
    *,
    db: AsyncSession,
    rag: RagService,
    query: str,
) -> list[FileSearchResultItem]:
    rows = await _load_known_files(db)
    matched_rows = _filename_matches(rows, query)

    ranked_rows: list[tuple[File, FileAnalysis | None]] = []
    seen_ids: set[str] = set()
    for row in matched_rows:
        _append_unique(ranked_rows, row, seen_ids)

    by_path: dict[str, tuple[File, FileAnalysis | None]] = {}
    for row in rows:
        file_record = row[0]
        by_path[file_record.current_path] = row
        by_path[file_record.original_path] = row

    for semantic_path in await _semantic_file_paths(rag, query):
        _append_unique(ranked_rows, by_path.get(semantic_path), seen_ids)

    return [_to_search_item(file_record, analysis) for file_record, analysis in ranked_rows]


@router.post("/files", response_model=FileSearchResponse)
async def search_files(
    body: FileSearchRequest,
    db: AsyncSession = Depends(get_db),
    rag: RagService = Depends(_get_rag),
) -> FileSearchResponse:
    """Search known files by basename first, then semantic RAG references."""

    query = body.query.strip().lower()
    if not query:
        return FileSearchResponse(results=[])

    results = await _search_known_files(db=db, rag=rag, query=query)
    return FileSearchResponse(results=results)
