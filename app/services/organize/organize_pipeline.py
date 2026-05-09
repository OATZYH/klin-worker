"""Organize pipeline orchestration.

Owns the per-file organize flow so the API router can stay focused on HTTP
concerns only.
"""

import asyncio
import hashlib
import json
import logging
import time
from collections import defaultdict
from typing import Any

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.ai_exceptions import (
    AiCapabilityUnavailableError,
    format_ai_capability_errors,
)
from app.db.models import Category, File, FileAnalysis
from app.models.response import CategoryScoreResponse, OrganizeFileResult
from app.observability.tracing import get_current_trace_id, observe, update_current_span
from app.services.ai.llm_client import llm_client
from app.services.ai.rag_service import RagService
from app.services.files.docling_parser import get_docling_profile_fingerprint
from app.services.history_service import HistoryService
from app.services.organize.background_ingest import BackgroundIngestWorker
from app.services.organize.classification_service import ClassificationService
from app.services.organize.organize_telemetry import OrganizeTelemetry
from app.services.organize.rename_service import RenameService
from app.services.organize.scanner_service import ScannerService
from app.services.summary.summary_service import SummaryService
from app.services.system_log_service import SystemLogService

logger = logging.getLogger(__name__)

_file_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


async def get_analysis_fingerprint(db: AsyncSession) -> str:
    """Compute a fingerprint of active category semantics + parser profile."""
    result = await db.execute(
        select(Category)
        .where(Category.is_active.is_(True))  # type: ignore[union-attr]
        .order_by(Category.id)
    )
    cats = result.scalars().all()
    parser_fingerprint = get_docling_profile_fingerprint("fast")
    if not cats:
        return hashlib.md5(f"no_categories::{parser_fingerprint}".encode()).hexdigest()
    cat_str = "||".join(f"{c.id}:{c.name}:{(c.description or '').strip()}" for c in cats)
    return hashlib.md5(f"{cat_str}::{parser_fingerprint}".encode()).hexdigest()


def elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 2)


def build_locked_result(filepath: str, reason: str) -> OrganizeFileResult:
    return OrganizeFileResult(
        file_id=f"locked::{hashlib.md5(filepath.encode()).hexdigest()}",
        suggested_names=[],
        categories=[],
        error=f"Skipped locked file: {reason}",
    )


async def _get_file_by_current_path(db: AsyncSession, file_path: str) -> File | None:
    """Look up a file row by its current known path."""
    current_path_column = getattr(File, "current_path")
    result = await db.execute(select(File).where(current_path_column == file_path))
    return result.scalar_one_or_none()


def _build_category_responses(
    scores: list[dict[str, Any]],
) -> list[CategoryScoreResponse]:
    """Convert raw cosine scores to API percentage responses."""
    return [
        CategoryScoreResponse(
            category_id=str(score["category_id"]),
            name=str(score["name"]),
            score=round(float(score["score"]) * 100, 1),
        )
        for score in scores
    ]


async def _log_pipeline_issue(
    *,
    db: AsyncSession,
    system_log_svc: SystemLogService,
    level: str,
    event_type: str,
    message: str,
    filepath: str,
    file_id: str | None = None,
    error: str | None = None,
    telemetry: OrganizeTelemetry | None = None,
) -> None:
    """Persist an operational warning/error for troubleshooting."""
    context = (
        telemetry.build_log_context(filepath=filepath, file_id=file_id, error=error)
        if telemetry is not None
        else {"filepath": filepath}
    )

    await system_log_svc.log(
        db=db,
        level=level,
        component="organize.pipeline",
        event_type=event_type,
        message=message,
        context=context,
        correlation_id=file_id,
    )


async def _collect_organize_ai_errors(
    rag: RagService,
) -> list[AiCapabilityUnavailableError]:
    """Collect live AI availability failures for organize flows."""
    errors: list[AiCapabilityUnavailableError] = []

    try:
        rag.ensure_ready()
    except AiCapabilityUnavailableError as exc:
        errors.append(exc)

    try:
        await llm_client.ensure_general_available()
    except AiCapabilityUnavailableError as exc:
        errors.append(exc)

    try:
        await llm_client.ensure_embedding_available(require_general_check=False)
    except AiCapabilityUnavailableError as exc:
        errors.append(exc)

    return errors


async def _build_ai_unavailable_result(
    *,
    db: AsyncSession,
    system_log_svc: SystemLogService,
    filepath: str,
    file_id: str,
    suggested_names: list[str],
    telemetry: OrganizeTelemetry,
    total_started_at: float,
    ai_errors: list[AiCapabilityUnavailableError],
    event_type: str,
    message: str,
) -> OrganizeFileResult:
    """Create a consistent per-file AI-unavailable organize result."""
    detail = format_ai_capability_errors(ai_errors)
    telemetry.ai_status = "unavailable"
    if any(error.capability == "rag" for error in ai_errors):
        telemetry.rag_status = "not_ready"
    elif telemetry.rag_status is None:
        telemetry.rag_status = "skipped_ai_unavailable"

    telemetry.record_total(elapsed_ms(total_started_at))

    await _log_pipeline_issue(
        db=db,
        system_log_svc=system_log_svc,
        level="WARNING",
        event_type=event_type,
        message=message,
        filepath=filepath,
        file_id=file_id,
        error=detail,
        telemetry=telemetry,
    )
    logger.warning(
        "Organize AI unavailable | file=%s | error=%s | timings=%s",
        filepath,
        detail,
        telemetry.timings_dict(),
    )
    return OrganizeFileResult(
        file_id=file_id,
        suggested_names=suggested_names,
        categories=[],
        error=detail,
    )


async def process_single_file(
    *,
    filepath: str,
    force: bool,
    db: AsyncSession,
    scanner: ScannerService,
    rag: RagService,
    classifier: ClassificationService,
    summary_svc: SummaryService,
    rename_svc: RenameService,
    history_svc: HistoryService,
    system_log_svc: SystemLogService,
    ingest: BackgroundIngestWorker,
) -> OrganizeFileResult:
    """Process one file with a per-path lock."""
    async with _file_locks[filepath]:
        return await _process_single_file_inner(
            filepath=filepath,
            force=force,
            db=db,
            scanner=scanner,
            rag=rag,
            classifier=classifier,
            summary_svc=summary_svc,
            rename_svc=rename_svc,
            history_svc=history_svc,
            system_log_svc=system_log_svc,
            ingest=ingest,
        )


@observe(name="organize.process_file", capture_input=False, capture_output=False)
async def _process_single_file_inner(
    filepath: str,
    force: bool,
    db: AsyncSession,
    scanner: ScannerService,
    rag: RagService,
    classifier: ClassificationService,
    summary_svc: SummaryService,
    rename_svc: RenameService,
    history_svc: HistoryService,
    system_log_svc: SystemLogService,
    ingest: BackgroundIngestWorker,
) -> OrganizeFileResult:
    """Core pipeline — assumes caller holds the per-file lock."""
    total_started_at = time.perf_counter()
    update_current_span(
        input={"filepath": filepath, "force": force},
        metadata={"trace_id": get_current_trace_id()},
    )
    telemetry = OrganizeTelemetry(rag_ready=rag.is_ready)

    step_started_at = time.perf_counter()
    scan = await scanner.scan(filepath)
    telemetry.record_timing("scan", elapsed_ms(step_started_at))

    if scan.error:
        telemetry.record_total(elapsed_ms(total_started_at))
        await _log_pipeline_issue(
            db=db,
            system_log_svc=system_log_svc,
            level="WARNING",
            event_type="organize_scan_failed",
            message="File scan failed during organize pipeline.",
            filepath=filepath,
            error=scan.error,
            telemetry=telemetry,
        )
        logger.warning(
            "Organize failed | file=%s | error=%s | timings=%s",
            filepath,
            scan.error,
            telemetry.timings_dict(),
        )
        return OrganizeFileResult(
            file_id="",
            suggested_names=[],
            categories=[],
            error=scan.error,
        )

    step_started_at = time.perf_counter()
    file_record = await _get_file_by_current_path(db, scan.original_path)

    is_new_file = False
    previous_hash = file_record.hash if file_record else None
    if file_record:
        await db.refresh(file_record, attribute_names=["analysis", "scores"])
        file_record.hash = scan.sha256
        file_record.size = scan.size_bytes
        file_record.extension = scan.extension
    else:
        file_record = File(
            original_path=scan.original_path,
            current_path=scan.original_path,
            hash=scan.sha256,
            size=scan.size_bytes,
            extension=scan.extension,
        )
        db.add(file_record)
        is_new_file = True

    await db.flush()
    file_changed = is_new_file or previous_hash != scan.sha256
    telemetry.is_new_file = is_new_file
    telemetry.file_changed = file_changed
    telemetry.record_timing("file_upsert", elapsed_ms(step_started_at))

    has_cached_analysis = False
    if not is_new_file:
        has_cached_analysis = (
            file_record.analysis is not None and file_record.analysis.summary is not None
        )

    current_analysis_fingerprint = await get_analysis_fingerprint(db)
    analysis_fingerprint_match = (
        has_cached_analysis
        and file_record.analysis.categories_hash == current_analysis_fingerprint  # type: ignore[union-attr]
    )

    if not file_changed and analysis_fingerprint_match and not force:
        step_started_at = time.perf_counter()
        telemetry.cached = True
        telemetry.cache_reason = "full_hit"
        telemetry.rag_status = "skipped_cached"

        cached_analysis = file_record.analysis
        assert cached_analysis is not None
        cached_scores = file_record.scores or []

        category_responses = []
        if cached_scores:
            cat_ids = [s.category_id for s in cached_scores]
            _cat_id_col = getattr(Category, "id")
            cat_result = await db.execute(select(Category).where(_cat_id_col.in_(cat_ids)))
            cat_map = {c.id: c for c in cat_result.scalars().all()}
            score_dicts = sorted(
                [
                    {
                        "category_id": s.category_id,
                        "name": cat_map[s.category_id].name,
                        "score": s.score,
                    }
                    for s in cached_scores
                    if s.category_id in cat_map
                ],
                key=lambda x: x["score"],
                reverse=True,
            )
            category_responses = _build_category_responses(score_dicts)
        else:
            score_dicts = []

        telemetry.record_timing("cache_lookup", elapsed_ms(step_started_at))

        cached_names: list[str] = []
        if cached_analysis.suggested_names:
            try:
                cached_names = json.loads(cached_analysis.suggested_names)
            except (json.JSONDecodeError, TypeError):
                cached_names = [cached_analysis.suggested_names]

        step_started_at = time.perf_counter()
        history_metadata = telemetry.build_history_metadata(
            suggested_names=cached_names,
            scores=score_dicts,
        )
        history_entry = await history_svc.log(
            db=db, file_id=file_record.id, action="organized_cached", metadata=history_metadata
        )
        telemetry.record_timing("history_log", elapsed_ms(step_started_at))
        telemetry.record_total(elapsed_ms(total_started_at))
        history_metadata = telemetry.build_history_metadata(
            suggested_names=cached_names,
            scores=score_dicts,
        )
        history_entry.metadata_json = json.dumps(history_metadata)
        await db.flush()

        logger.info(
            "Organize cached (full hit) | file=%s | timings=%s",
            scan.original_path,
            telemetry.timings_dict(),
        )
        update_current_span(
            metadata=telemetry.build_trace_metadata(),
            output={"category_count": len(category_responses), "cached": True},
        )
        return OrganizeFileResult(
            file_id=file_record.id,
            suggested_names=cached_names,
            categories=category_responses,
        )

    if not file_changed and has_cached_analysis and not force:
        step_started_at = time.perf_counter()
        telemetry.cached = "partial"
        telemetry.cache_reason = "analysis_inputs_changed"
        telemetry.rag_status = "skipped_unchanged"

        cached_analysis = file_record.analysis
        assert cached_analysis is not None

        cached_names = []
        if cached_analysis.suggested_names:
            try:
                cached_names = json.loads(cached_analysis.suggested_names)
            except (json.JSONDecodeError, TypeError):
                cached_names = [cached_analysis.suggested_names]

        ai_check_started_at = time.perf_counter()
        ai_errors = await _collect_organize_ai_errors(rag)
        telemetry.record_timing("ai_check", elapsed_ms(ai_check_started_at))
        if ai_errors:
            return await _build_ai_unavailable_result(
                db=db,
                system_log_svc=system_log_svc,
                filepath=scan.original_path,
                file_id=file_record.id,
                suggested_names=cached_names,
                telemetry=telemetry,
                total_started_at=total_started_at,
                ai_errors=ai_errors,
                event_type="organize_reclassify_ai_unavailable",
                message="AI capabilities unavailable during organize re-classification.",
            )
        telemetry.ai_status = "ready"

        scores: list[dict] = []
        try:
            file_embedding = await classifier.get_file_embedding(
                scan.original_path, summary=cached_analysis.summary
            )
            if file_embedding:
                scores = await classifier.classify_with_embedding(
                    file_id=file_record.id, file_embedding=file_embedding, db=db
                )
        except AiCapabilityUnavailableError as exc:
            return await _build_ai_unavailable_result(
                db=db,
                system_log_svc=system_log_svc,
                filepath=scan.original_path,
                file_id=file_record.id,
                suggested_names=cached_names,
                telemetry=telemetry,
                total_started_at=total_started_at,
                ai_errors=[exc],
                event_type="organize_reclassify_ai_unavailable",
                message="AI capabilities unavailable during organize re-classification.",
            )
        except Exception as exc:
            logger.error("Re-classify failed for %s: %s", scan.original_path, exc)
            await _log_pipeline_issue(
                db=db,
                system_log_svc=system_log_svc,
                level="WARNING",
                event_type="organize_reclassify_failed",
                message="Category re-classification failed during organize pipeline.",
                filepath=scan.original_path,
                file_id=file_record.id,
                error=str(exc),
                telemetry=telemetry,
            )
        telemetry.record_timing("reclassify", elapsed_ms(step_started_at))

        cached_analysis.categories_hash = current_analysis_fingerprint
        await db.flush()

        category_responses = _build_category_responses(scores)

        step_started_at = time.perf_counter()
        history_metadata = telemetry.build_history_metadata(
            suggested_names=cached_names,
            scores=scores,
        )
        history_entry = await history_svc.log(
            db=db,
            file_id=file_record.id,
            action="organized_reclassified",
            metadata=history_metadata,
        )
        telemetry.record_timing("history_log", elapsed_ms(step_started_at))
        telemetry.record_total(elapsed_ms(total_started_at))
        history_metadata = telemetry.build_history_metadata(
            suggested_names=cached_names,
            scores=scores,
        )
        history_entry.metadata_json = json.dumps(history_metadata)
        await db.flush()

        logger.info(
            "Organize partial cache (re-classified) | file=%s | timings=%s",
            scan.original_path,
            telemetry.timings_dict(),
        )
        update_current_span(
            metadata=telemetry.build_trace_metadata(),
            output={"category_count": len(category_responses), "cached": True},
        )
        return OrganizeFileResult(
            file_id=file_record.id,
            suggested_names=cached_names,
            categories=category_responses,
        )

    step_started_at = time.perf_counter()
    ai_errors = await _collect_organize_ai_errors(rag)
    telemetry.record_timing("ai_check", elapsed_ms(step_started_at))
    if ai_errors:
        return await _build_ai_unavailable_result(
            db=db,
            system_log_svc=system_log_svc,
            filepath=scan.original_path,
            file_id=file_record.id,
            suggested_names=[],
            telemetry=telemetry,
            total_started_at=total_started_at,
            ai_errors=ai_errors,
            event_type="organize_ai_unavailable",
            message="AI capabilities unavailable during organize pipeline.",
        )
    telemetry.ai_status = "ready"

    step_started_at = time.perf_counter()
    prepared_ingest = None
    if not rag.is_ready:
        telemetry.rag_status = "not_ready"
    elif not file_changed and not force:
        telemetry.rag_status = "skipped_unchanged"
    else:
        prepared_ingest = await ingest.prepare(
            filepath=scan.original_path,
            trace_id=get_current_trace_id(),
        )
        telemetry.rag_status = prepared_ingest.ingest_status
    telemetry.record_timing("rag_enqueue", elapsed_ms(step_started_at))

    step_started_at = time.perf_counter()
    summary_text: str | None = None
    try:
        extracted_text = prepared_ingest.extracted_text if prepared_ingest else None
        summary_text = await summary_svc.summarise(
            scan.original_path,
            extracted_text=extracted_text,
        )
    except AiCapabilityUnavailableError as exc:
        return await _build_ai_unavailable_result(
            db=db,
            system_log_svc=system_log_svc,
            filepath=scan.original_path,
            file_id=file_record.id,
            suggested_names=[],
            telemetry=telemetry,
            total_started_at=total_started_at,
            ai_errors=[exc],
            event_type="organize_summary_ai_unavailable",
            message="AI capabilities unavailable during organize summary generation.",
        )
    except Exception as exc:
        logger.error("Summary failed for %s: %s", scan.original_path, exc)
        await _log_pipeline_issue(
            db=db,
            system_log_svc=system_log_svc,
            level="WARNING",
            event_type="organize_summary_failed",
            message="AI summary generation failed during organize pipeline.",
            filepath=scan.original_path,
            file_id=file_record.id,
            error=str(exc),
            telemetry=telemetry,
        )
    telemetry.record_timing("summary", elapsed_ms(step_started_at))

    step_started_at = time.perf_counter()
    suggested_names: list[str] = []
    try:
        suggested_names = await rename_svc.suggest_names(
            original_name=scan.file_name,
            extension=scan.extension,
            summary=summary_text,
        )
    except AiCapabilityUnavailableError as exc:
        return await _build_ai_unavailable_result(
            db=db,
            system_log_svc=system_log_svc,
            filepath=scan.original_path,
            file_id=file_record.id,
            suggested_names=[],
            telemetry=telemetry,
            total_started_at=total_started_at,
            ai_errors=[exc],
            event_type="organize_rename_ai_unavailable",
            message="AI capabilities unavailable during organize rename generation.",
        )
    except Exception as exc:
        logger.error("Rename failed for %s: %s", scan.original_path, exc)
        await _log_pipeline_issue(
            db=db,
            system_log_svc=system_log_svc,
            level="WARNING",
            event_type="organize_rename_failed",
            message="Filename suggestion generation failed during organize pipeline.",
            filepath=scan.original_path,
            file_id=file_record.id,
            error=str(exc),
            telemetry=telemetry,
        )
    telemetry.record_timing("rename", elapsed_ms(step_started_at))

    step_started_at = time.perf_counter()
    names_json = json.dumps(suggested_names) if suggested_names else None
    if not is_new_file and file_record.analysis is not None:
        file_record.analysis.summary = summary_text
        file_record.analysis.suggested_names = names_json
        file_record.analysis.categories_hash = current_analysis_fingerprint
    else:
        analysis = FileAnalysis(
            file_id=file_record.id,
            summary=summary_text,
            suggested_names=names_json,
            categories_hash=current_analysis_fingerprint,
        )
        db.add(analysis)

    await db.flush()
    telemetry.record_timing("analysis_save", elapsed_ms(step_started_at))

    step_started_at = time.perf_counter()
    scores: list[dict] = []
    try:
        file_embedding = await classifier.get_file_embedding(
            scan.original_path, summary=summary_text
        )
        if file_embedding:
            scores = await classifier.classify_with_embedding(
                file_id=file_record.id,
                file_embedding=file_embedding,
                db=db,
            )
    except AiCapabilityUnavailableError as exc:
        return await _build_ai_unavailable_result(
            db=db,
            system_log_svc=system_log_svc,
            filepath=scan.original_path,
            file_id=file_record.id,
            suggested_names=suggested_names,
            telemetry=telemetry,
            total_started_at=total_started_at,
            ai_errors=[exc],
            event_type="organize_classify_ai_unavailable",
            message="AI capabilities unavailable during organize classification.",
        )
    except Exception as exc:
        logger.error("Classification failed for %s: %s", scan.original_path, exc)
        await _log_pipeline_issue(
            db=db,
            system_log_svc=system_log_svc,
            level="WARNING",
            event_type="organize_classification_failed",
            message="Classification failed during organize pipeline.",
            filepath=scan.original_path,
            file_id=file_record.id,
            error=str(exc),
            telemetry=telemetry,
        )
    telemetry.record_timing("classify", elapsed_ms(step_started_at))

    category_responses = _build_category_responses(scores)

    telemetry.cache_reason = "full_run"
    history_metadata = telemetry.build_history_metadata(
        suggested_names=suggested_names,
        scores=scores,
    )
    step_started_at = time.perf_counter()
    history_entry = await history_svc.log(
        db=db,
        file_id=file_record.id,
        action="organized",
        metadata=history_metadata,
    )
    telemetry.record_timing("history_log", elapsed_ms(step_started_at))
    telemetry.record_total(elapsed_ms(total_started_at))
    history_metadata = telemetry.build_history_metadata(
        suggested_names=suggested_names,
        scores=scores,
    )
    history_entry.metadata_json = json.dumps(history_metadata)
    await db.flush()

    logger.info(
        "Organize complete | file=%s | new=%s | changed=%s | rag=%s | timings=%s",
        scan.original_path,
        is_new_file,
        file_changed,
        telemetry.rag_status,
        telemetry.timings_dict(),
    )
    update_current_span(
        metadata=telemetry.build_trace_metadata(),
        output={"category_count": len(category_responses), "suggestion_count": len(suggested_names)},
    )

    return OrganizeFileResult(
        file_id=file_record.id,
        suggested_names=suggested_names,
        categories=category_responses,
    )