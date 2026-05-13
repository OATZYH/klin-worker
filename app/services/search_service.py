"""Search pipeline service for hybrid filename and RAG chunk retrieval."""

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.db.models import File, FileAnalysis
from app.models.response import FileSearchResponse, FileSearchResultItem
from app.observability.tracing import (
    observe,
    start_as_current_observation,
    suppress_trace_text_payloads,
    update_current_span,
)
from app.services.ai.rag_service import RagService
from app.services.organize.background_ingest import BackgroundIngestWorker

logger = logging.getLogger(__name__)

DEFAULT_SEARCH_LIMIT = 20
SemanticStatus = Literal["ready", "pending", "degraded", "not_ready"]


@dataclass(frozen=True, slots=True)
class SemanticSearchOutcome:
    paths: list[str]
    status: SemanticStatus
    error: str | None = None
    error_category: str | None = None


@dataclass(frozen=True, slots=True)
class SemanticPayloadDiagnostics:
    paths: list[str]
    reference_count: int = 0
    chunk_count: int = 0
    accepted_chunk_count: int = 0
    filtered_chunk_count: int = 0
    lexical_match_count: int = 0
    score_match_count: int = 0
    score_only_match_count: int = 0
    invalid_file_path_count: int = 0
    semantic_min_score: float | None = None
    semantic_score_only_min_score: float | None = None
    payload_is_dict: bool = True
    data_is_dict: bool = True


def _file_name(path: str) -> str:
    return Path(path).name


def _normalize_filename_search_text(value: str) -> str:
    """Normalize separators so terms match underscores/dashes/dots in filenames."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.lower())).strip()


def _search_tokens(value: str) -> list[str]:
    """Normalize query/text into word tokens, including simple plural folding."""
    raw_tokens = re.findall(r"[a-z0-9]+", value.lower())
    tokens: list[str] = []
    for token in raw_tokens:
        if token.endswith("s") and len(token) > 3:
            tokens.append(token[:-1])
        else:
            tokens.append(token)
    return tokens


def _has_ordered_token_phrase(query: str, text: str) -> bool:
    query_tokens = _search_tokens(query)
    text_tokens = _search_tokens(text)
    if not query_tokens or not text_tokens:
        return False
    if len(query_tokens) == 1:
        return query_tokens[0] in set(text_tokens)
    phrase_length = len(query_tokens)
    return any(
        text_tokens[index : index + phrase_length] == query_tokens
        for index in range(0, len(text_tokens) - phrase_length + 1)
    )


def _chunk_score(chunk: dict[str, Any]) -> float:
    raw_score = chunk.get("distance", chunk.get("score", 0.0))
    try:
        return float(raw_score)
    except (TypeError, ValueError):
        return 0.0


def _chunk_text_for_lexical_match(chunk: dict[str, Any], stored_chunk: Any = None) -> str:
    values = [
        chunk.get("content"),
        chunk.get("file_path"),
        chunk.get("full_doc_id"),
    ]
    if isinstance(stored_chunk, dict):
        values.extend([
            stored_chunk.get("content"),
            stored_chunk.get("file_path"),
            stored_chunk.get("full_doc_id"),
        ])
    elif stored_chunk is not None:
        values.extend([
            getattr(stored_chunk, "content", None),
            getattr(stored_chunk, "file_path", None),
            getattr(stored_chunk, "full_doc_id", None),
        ])
    return "\n".join(str(value) for value in values if value)


def _chunk_file_path(chunk: dict[str, Any], stored_chunk: Any = None) -> str | None:
    file_path = chunk.get("file_path")
    if isinstance(file_path, str) and file_path:
        return file_path
    if isinstance(stored_chunk, dict):
        file_path = stored_chunk.get("file_path")
    elif stored_chunk is not None:
        file_path = getattr(stored_chunk, "file_path", None)
    return file_path if isinstance(file_path, str) and file_path else None


async def _load_stored_chunk(lightrag: Any, chunk_id: str | None) -> Any:
    if not chunk_id:
        return None
    text_chunks = getattr(lightrag, "text_chunks", None)
    if text_chunks is None or not hasattr(text_chunks, "get_by_id"):
        return None
    return await text_chunks.get_by_id(chunk_id)


def _normalize_query(value: str) -> str:
    return value.strip().lower()


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
    normalized_query = _normalize_filename_search_text(query)
    return [
        row
        for row in rows
        if normalized_query
        and normalized_query in _normalize_filename_search_text(_file_name(row[0].current_path))
    ]


def _record_value(record: Any, key: str) -> Any:
    if isinstance(record, dict):
        return record.get(key)
    return getattr(record, key, None)


def _semantic_status_from_doc_status(rag: RagService) -> SemanticSearchOutcome | None:
    """Surface persisted LightRAG failed/pending docs in search freshness metadata."""
    rag_engine = getattr(rag, "_rag", None)
    lightrag = getattr(rag_engine, "lightrag", None)
    doc_status = getattr(lightrag, "doc_status", None)
    storage_data = getattr(doc_status, "_data", None)
    if not isinstance(storage_data, dict):
        return None

    failed_count = 0
    pending_count = 0
    first_error: str | None = None
    for record in storage_data.values():
        status = str(_record_value(record, "status") or "").lower().rsplit(".", 1)[-1]
        if status == "failed":
            failed_count += 1
            if first_error is None:
                first_error = str(
                    _record_value(record, "error_msg")
                    or "One or more RAG documents failed indexing."
                )
        elif status in {"pending", "processing"}:
            pending_count += 1

    if failed_count > 0:
        error = first_error or f"{failed_count} RAG document(s) failed indexing."
        return SemanticSearchOutcome(
            paths=[],
            status="degraded",
            error=error,
            error_category="lightrag_doc_status_failed",
        )
    if pending_count > 0:
        return SemanticSearchOutcome(
            paths=[],
            status="pending",
            error=None,
            error_category="lightrag_doc_status_pending",
        )
    return None


def _semantic_payload_diagnostics(payload: Any) -> SemanticPayloadDiagnostics:
    if not isinstance(payload, dict):
        return SemanticPayloadDiagnostics(
            paths=[],
            payload_is_dict=False,
            data_is_dict=False,
        )

    data = payload.get("data")
    if not isinstance(data, dict):
        return SemanticPayloadDiagnostics(paths=[], data_is_dict=False)

    ordered_paths: list[str] = []
    seen: set[str] = set()
    reference_count = 0
    chunk_count = 0
    invalid_file_path_count = 0

    references = data.get("references")
    if isinstance(references, list):
        for reference in references:
            reference_count += 1
            if not isinstance(reference, dict):
                invalid_file_path_count += 1
                continue
            file_path = reference.get("file_path")
            if not isinstance(file_path, str) or not file_path:
                invalid_file_path_count += 1
                continue
            if file_path not in seen:
                ordered_paths.append(file_path)
                seen.add(file_path)

    chunks = data.get("chunks")
    if isinstance(chunks, list):
        for chunk in chunks:
            chunk_count += 1
            if not isinstance(chunk, dict):
                invalid_file_path_count += 1
                continue
            file_path = chunk.get("file_path")
            if not isinstance(file_path, str) or not file_path:
                invalid_file_path_count += 1
                continue
            if file_path not in seen:
                ordered_paths.append(file_path)
                seen.add(file_path)

    return SemanticPayloadDiagnostics(
        paths=ordered_paths,
        reference_count=reference_count,
        chunk_count=chunk_count,
        invalid_file_path_count=invalid_file_path_count,
    )


def _semantic_paths_from_payload(payload: Any) -> list[str]:
    return _semantic_payload_diagnostics(payload).paths


async def _semantic_chunk_diagnostics(
    lightrag: Any,
    chunks: Any,
    query: str,
) -> SemanticPayloadDiagnostics:
    if not isinstance(chunks, list):
        return SemanticPayloadDiagnostics(
            paths=[],
            payload_is_dict=False,
            data_is_dict=False,
            semantic_min_score=settings.search_semantic_min_score,
        )

    ordered_paths: list[str] = []
    seen: set[str] = set()
    invalid_file_path_count = 0
    accepted_chunk_count = 0
    filtered_chunk_count = 0
    lexical_match_count = 0
    score_match_count = 0
    score_only_match_count = 0

    for chunk in chunks:
        if not isinstance(chunk, dict):
            invalid_file_path_count += 1
            filtered_chunk_count += 1
            continue

        chunk_id = chunk.get("id")
        stored_chunk = await _load_stored_chunk(
            lightrag,
            chunk_id if isinstance(chunk_id, str) else None,
        )
        lexical_text = _chunk_text_for_lexical_match(chunk, stored_chunk)
        lexical_match = _has_ordered_token_phrase(query, lexical_text)
        chunk_score = _chunk_score(chunk)
        score_match = chunk_score >= settings.search_semantic_min_score
        if lexical_match:
            lexical_match_count += 1
        if score_match:
            score_match_count += 1

        if lexical_match:
            pass  # always accept lexical hits
        elif chunk_score >= settings.search_semantic_score_only_min_score:
            score_only_match_count += 1
        else:
            filtered_chunk_count += 1
            continue

        file_path = _chunk_file_path(chunk, stored_chunk)
        if not file_path:
            invalid_file_path_count += 1
            filtered_chunk_count += 1
            continue

        accepted_chunk_count += 1
        if file_path not in seen:
            ordered_paths.append(file_path)
            seen.add(file_path)

    return SemanticPayloadDiagnostics(
        paths=ordered_paths,
        chunk_count=len(chunks),
        accepted_chunk_count=accepted_chunk_count,
        filtered_chunk_count=filtered_chunk_count,
        lexical_match_count=lexical_match_count,
        score_match_count=score_match_count,
        score_only_match_count=score_only_match_count,
        invalid_file_path_count=invalid_file_path_count,
        semantic_min_score=settings.search_semantic_min_score,
        semantic_score_only_min_score=settings.search_semantic_score_only_min_score,
    )


def _semantic_trace_payload(
    outcome: SemanticSearchOutcome,
    diagnostics: SemanticPayloadDiagnostics | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": outcome.status,
        "error_category": outcome.error_category,
        "semantic_raw_path_count": len(outcome.paths),
    }
    if diagnostics is not None:
        payload.update(
            {
                "reference_count": diagnostics.reference_count,
                "chunk_count": diagnostics.chunk_count,
                "accepted_chunk_count": diagnostics.accepted_chunk_count,
                "filtered_chunk_count": diagnostics.filtered_chunk_count,
                "lexical_match_count": diagnostics.lexical_match_count,
                "score_match_count": diagnostics.score_match_count,
                "score_only_match_count": diagnostics.score_only_match_count,
                "extracted_unique_path_count": len(diagnostics.paths),
                "invalid_file_path_count": diagnostics.invalid_file_path_count,
                "semantic_min_score": diagnostics.semantic_min_score,
                "semantic_score_only_min_score": diagnostics.semantic_score_only_min_score,
                "payload_is_dict": diagnostics.payload_is_dict,
                "data_is_dict": diagnostics.data_is_dict,
            }
        )
    return payload


@observe(name="search.semantic_lightrag", capture_input=False, capture_output=False)
async def _semantic_file_paths(
    rag: RagService,
    query: str,
    *,
    limit: int = DEFAULT_SEARCH_LIMIT,
) -> SemanticSearchOutcome:
    query_params = {
        "top_k": max(limit * 3, limit),
        "semantic_min_score": settings.search_semantic_min_score,
    }
    update_current_span(
        input={
            "query": query,
            "query_length": len(query),
            "limit": limit,
            "query_params": query_params,
        },
        metadata={"feature": "search", "component": "semantic_lightrag"},
    )

    if not getattr(rag, "is_ready", False):
        outcome = SemanticSearchOutcome(
            paths=[],
            status="not_ready",
            error="RAG service is not ready.",
            error_category="rag_not_ready",
        )
        update_current_span(
            output=_semantic_trace_payload(outcome),
            level="WARNING",
            status_message="Semantic search skipped: RAG service is not ready.",
        )
        return outcome

    try:
        rag_engine = getattr(rag, "_rag", None)
        if rag_engine is None:
            outcome = SemanticSearchOutcome(
                paths=[],
                status="not_ready",
                error="RAG engine is not available.",
                error_category="rag_engine_unavailable",
            )
            update_current_span(
                output=_semantic_trace_payload(outcome),
                level="WARNING",
                status_message="Semantic search skipped: RAG engine is not available.",
            )
            return outcome

        if hasattr(rag_engine, "_ensure_lightrag_initialized"):
            init_result = await rag_engine._ensure_lightrag_initialized()  # noqa: SLF001
            init_success = not isinstance(init_result, dict) or init_result.get("success", True)
            if isinstance(init_result, dict) and not init_result.get("success", True):
                error = str(init_result.get("error") or "LightRAG initialization failed.")
                outcome = SemanticSearchOutcome(
                    paths=[],
                    status="degraded",
                    error=error,
                    error_category="lightrag_init_failed",
                )
                update_current_span(
                    output={
                        **_semantic_trace_payload(outcome),
                        "lightrag_init_success": bool(init_success),
                        "lightrag_init_error": error,
                    },
                    level="WARNING",
                    status_message=f"Semantic search degraded: {error}",
                )
                return outcome
        else:
            init_success = None

        lightrag = getattr(rag_engine, "lightrag", None)
        chunks_vdb = getattr(lightrag, "chunks_vdb", None) if lightrag is not None else None
        if chunks_vdb is None or not hasattr(chunks_vdb, "query"):
            outcome = SemanticSearchOutcome(
                paths=[],
                status="not_ready",
                error="LightRAG chunk vector API is not available.",
                error_category="lightrag_chunk_query_unavailable",
            )
            update_current_span(
                output=_semantic_trace_payload(outcome),
                level="WARNING",
                status_message="Semantic search skipped: LightRAG chunk vector API is unavailable.",
            )
            return outcome

        with suppress_trace_text_payloads():
            chunks = await chunks_vdb.query(query, top_k=query_params["top_k"])

        with start_as_current_observation(
            name="search.semantic_chunk_filter",
            as_type="span",
            input={
                "chunk_count": len(chunks) if isinstance(chunks, list) else None,
                "semantic_min_score": settings.search_semantic_min_score,
            },
            metadata={"feature": "search", "component": "semantic_chunk_filter"},
        ):
            diagnostics = await _semantic_chunk_diagnostics(lightrag, chunks, query)
            update_current_span(
                output={
                    "chunk_count": diagnostics.chunk_count,
                    "accepted_chunk_count": diagnostics.accepted_chunk_count,
                    "filtered_chunk_count": diagnostics.filtered_chunk_count,
                    "lexical_match_count": diagnostics.lexical_match_count,
                    "score_match_count": diagnostics.score_match_count,
                    "score_only_match_count": diagnostics.score_only_match_count,
                    "extracted_unique_path_count": len(diagnostics.paths),
                    "invalid_file_path_count": diagnostics.invalid_file_path_count,
                    "semantic_min_score": diagnostics.semantic_min_score,
                    "semantic_score_only_min_score": diagnostics.semantic_score_only_min_score,
                }
            )

        outcome = SemanticSearchOutcome(paths=diagnostics.paths, status="ready")
        semantic_output = _semantic_trace_payload(outcome, diagnostics)
        if init_success is not None:
            semantic_output["lightrag_init_success"] = bool(init_success)
        update_current_span(output=semantic_output)
        return outcome
    except Exception as exc:
        logger.warning("Semantic search failed; returning filename matches only: %s", exc)
        outcome = SemanticSearchOutcome(
            paths=[],
            status="degraded",
            error=str(exc),
            error_category=type(exc).__name__,
        )
        update_current_span(
            output=_semantic_trace_payload(outcome),
            level="WARNING",
            status_message="Semantic search degraded; returning filename matches only.",
        )
        return outcome


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
    ingest: BackgroundIngestWorker | None,
    query: str,
) -> FileSearchResponse:
    with start_as_current_observation(
        name="search.load_known_files",
        as_type="span",
        metadata={"feature": "search", "component": "sqlite"},
    ):
        rows = await _load_known_files(db)
        analysis_count = sum(1 for _, analysis in rows if analysis is not None)
        update_current_span(
            output={
                "known_file_count": len(rows),
                "analysis_count": analysis_count,
                "missing_analysis_count": len(rows) - analysis_count,
            }
        )

    with start_as_current_observation(
        name="search.filename_match",
        as_type="span",
        input={
            "query": query,
            "normalized_filename_query": _normalize_filename_search_text(query),
            "candidate_count": len(rows),
        },
        metadata={"feature": "search", "component": "filename_match"},
    ):
        matched_rows = _filename_matches(rows, query)
        update_current_span(output={"filename_match_count": len(matched_rows)})

    ranked_rows: list[tuple[File, FileAnalysis | None]] = []
    seen_ids: set[str] = set()
    for row in matched_rows:
        _append_unique(ranked_rows, row, seen_ids)

    by_path: dict[str, tuple[File, FileAnalysis | None]] = {}
    by_basename: dict[str, tuple[File, FileAnalysis | None]] = {}
    for row in rows:
        file_record = row[0]
        by_path[file_record.current_path] = row
        by_path[file_record.original_path] = row
        basename = Path(file_record.current_path).name
        if basename not in by_basename:
            by_basename[basename] = row

    pending_count = ingest.pending_count() if ingest is not None else 0
    semantic = await _semantic_file_paths(rag, query)
    semantic_status = semantic.status
    doc_status_outcome = _semantic_status_from_doc_status(rag)
    semantic_error = semantic.error
    semantic_error_category = semantic.error_category
    if doc_status_outcome is not None and semantic_status == "ready":
        semantic_status = doc_status_outcome.status
        semantic_error = doc_status_outcome.error
        semantic_error_category = doc_status_outcome.error_category
    if pending_count > 0 and semantic_status == "ready":
        semantic_status = "pending"

    with start_as_current_observation(
        name="search.map_semantic_paths",
        as_type="span",
        input={
            "semantic_raw_path_count": len(semantic.paths),
            "known_path_key_count": len(by_path),
        },
        metadata={"feature": "search", "component": "semantic_path_mapping"},
    ):
        semantic_matched_count = 0
        semantic_missing_count = 0
        semantic_duplicate_count = 0
        before_count = len(ranked_rows)
        for semantic_path in semantic.paths:
            row = by_path.get(semantic_path) or by_basename.get(Path(semantic_path).name)
            if row is None:
                semantic_missing_count += 1
            else:
                semantic_matched_count += 1
                if row[0].id in seen_ids:
                    semantic_duplicate_count += 1
            _append_unique(ranked_rows, row, seen_ids)
        update_current_span(
            output={
                "semantic_matched_known_file_count": semantic_matched_count,
                "semantic_missing_known_file_count": semantic_missing_count,
                "semantic_duplicate_result_count": semantic_duplicate_count,
                "semantic_added_result_count": len(ranked_rows) - before_count,
            }
        )

    with start_as_current_observation(
        name="search.finalize_results",
        as_type="span",
        input={
            "ranked_row_count": len(ranked_rows),
            "semantic_status": semantic_status,
        },
        metadata={"feature": "search", "component": "result_formatting"},
    ):
        response = FileSearchResponse(
            results=[
                _to_search_item(file_record, analysis)
                for file_record, analysis in ranked_rows
            ],
            semantic_status=semantic_status,
            semantic_error=semantic_error,
            indexing_pending_count=pending_count,
        )
        summary = {
            "known_file_count": len(rows),
            "filename_match_count": len(matched_rows),
            "semantic_status": semantic_status,
            "semantic_error_category": semantic_error_category,
            "pending_ingest_count": pending_count,
            "semantic_raw_path_count": len(semantic.paths),
            "semantic_matched_known_file_count": semantic_matched_count,
            "semantic_missing_known_file_count": semantic_missing_count,
            "semantic_duplicate_result_count": semantic_duplicate_count,
            "final_result_count": len(response.results),
        }
        update_current_span(output=summary)

    update_current_span(output=summary, metadata={"semantic_status": semantic_status})
    return response


async def search_files(
    *,
    db: AsyncSession,
    rag: RagService,
    ingest: BackgroundIngestWorker | None,
    raw_query: str,
) -> FileSearchResponse:
    """Search known files by basename first, then semantic RAG references."""

    with start_as_current_observation(
        name="search.normalize_query",
        as_type="span",
        input={"query": raw_query, "query_length": len(raw_query)},
        metadata={"feature": "search", "component": "query_normalization"},
    ):
        query = _normalize_query(raw_query)
        update_current_span(
            output={
                "normalized_query": query,
                "normalized_query_length": len(query),
                "is_empty_query": not bool(query),
            }
        )

    update_current_span(
        input={
            "query": raw_query,
            "normalized_query": query,
            "query_length": len(raw_query),
            "normalized_query_length": len(query),
            "is_empty_query": not bool(query),
        },
        metadata={"feature": "search", "component": "search_service"},
    )
    if not query:
        pending_count = ingest.pending_count() if ingest is not None else 0
        status: SemanticStatus = "pending" if pending_count > 0 else "ready"
        if not getattr(rag, "is_ready", False):
            status = "not_ready"
        response = FileSearchResponse(
            results=[],
            semantic_status=status,
            indexing_pending_count=pending_count,
        )
        update_current_span(
            output={
                "known_file_count": 0,
                "filename_match_count": 0,
                "semantic_status": status,
                "semantic_error_category": (
                    "rag_not_ready" if not getattr(rag, "is_ready", False) else None
                ),
                "pending_ingest_count": pending_count,
                "semantic_raw_path_count": 0,
                "semantic_matched_known_file_count": 0,
                "semantic_missing_known_file_count": 0,
                "final_result_count": 0,
                "branch": "empty_query",
            },
        )
        return response

    return await _search_known_files(db=db, rag=rag, ingest=ingest, query=query)
