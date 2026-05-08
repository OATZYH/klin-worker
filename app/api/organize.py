"""
Organize API router.

POST /api/organize — the main endpoint.
  1. Scan files for metadata
  2. Store file records in SQLite
  3. **Cache check** — return cached results instantly for unchanged files
    4. Prepare fast summary context + queue background RAG ingestion
    5. Generate summary from fast parsed text
    6. Generate rename suggestion (needs summary)
    7. Classify using summary-enriched embedding
  8. Log history
  9. Return structured results

Optimisations (v0.3):
  • Cache-first: unchanged files return cached analysis in <100 ms.
    • Background RAG: rich ingestion is queued, not awaited.
    • Fast docling: request-time parsing stays lightweight.
  • Per-file lock: prevents race conditions on concurrent identical requests.
  • Robust upsert: FileAnalysis checked by existence, not by is_new_file flag.
"""

import asyncio
import hashlib
import json
import logging
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import Category, CategoryScore, DetectedCalendarEvent, File, FileAnalysis
from app.core.ai_exceptions import (
    AiCapabilityUnavailableError,
    format_ai_capability_errors,
)
from app.db.session import get_db
from app.models.request import (
    ApplyOrganizeDecisionRequest,
    ApplySelectedCategoryRequest,
    OrganizeRequest,
)
from app.models.response import (
    ApplyOrganizeDecisionResponse,
    CategoryScoreResponse,
    FileAnalysisResponse,
    OrganizeFileResult,
    OrganizeResponse,
)
from app.services.background_ingest import BackgroundIngestWorker
from app.services.categories.classification_service import ClassificationService
from app.services.history_service import HistoryService
from app.services.ai.calendar_extraction_service import (
    CalendarEventDto,
    CalendarExtractionService,
    calendar_extraction_service,
)
from app.services.ai.llm_client import llm_client
from app.services.ai.rag_service import RagService
from app.services.ai.rename_service import RenameService
from app.services.files.docling_parser import get_docling_profile_fingerprint
from app.services.files.scanner_service import ScannerService
from app.services.ai.summary_service import SummaryService
from app.services.system_log_service import SystemLogService
from app.services.lock_settings_service import LockSettingsService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["organize"])


# ── Analysis fingerprint — for cache invalidation ────────────────────────


async def _get_analysis_fingerprint(db: AsyncSession) -> str:
    """Compute a fingerprint of active category semantics + parser profile.

    Stored in the existing ``file_analysis.categories_hash`` column so cache
    invalidation also reacts to fast docling profile changes.
    """
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


# ── Per-file lock to prevent concurrent processing of the same path ──────

_file_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def _elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 2)


def _build_locked_result(filepath: str, reason: str) -> OrganizeFileResult:
    return OrganizeFileResult(
        file_id=f"locked::{hashlib.md5(filepath.encode()).hexdigest()}",
        analysis=FileAnalysisResponse(suggested_names=[]),
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


def _build_history_metadata(
    *,
    suggested_names: list[str],
    scores: list[dict[str, Any]],
    pipeline: dict[str, str | bool | None],
    timings: dict[str, float],
) -> dict[str, Any]:
    """Build a stable history payload for all organize event variants."""
    return {
        "suggested_names": suggested_names,
        "all_scores": [
            {
                "category_id": str(score["category_id"]),
                "name": str(score["name"]),
                "score": float(score["score"]),
            }
            for score in scores
        ],
        "pipeline": dict(pipeline),
        "timings": dict(timings),
    }


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
    pipeline: dict[str, str | bool | None] | None = None,
    timings: dict[str, float] | None = None,
) -> None:
    """Persist an operational warning/error for troubleshooting."""
    context: dict[str, Any] = {"filepath": filepath}
    if file_id:
        context["file_id"] = file_id
    if error:
        context["error"] = error
    if pipeline:
        context["pipeline"] = dict(pipeline)
    if timings:
        context["timings"] = dict(timings)

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
    pipeline: dict[str, str | bool | None],
    timings: dict[str, float],
    total_started_at: float,
    ai_errors: list[AiCapabilityUnavailableError],
    event_type: str,
    message: str,
) -> OrganizeFileResult:
    """Create a consistent per-file AI-unavailable organize result."""
    detail = format_ai_capability_errors(ai_errors)
    pipeline["ai_status"] = "unavailable"
    if any(error.capability == "rag" for error in ai_errors):
        pipeline["rag_status"] = "not_ready"
    elif pipeline.get("rag_status") is None:
        pipeline["rag_status"] = "skipped_ai_unavailable"

    timings["total_ms"] = _elapsed_ms(total_started_at)

    await _log_pipeline_issue(
        db=db,
        system_log_svc=system_log_svc,
        level="WARNING",
        event_type=event_type,
        message=message,
        filepath=filepath,
        file_id=file_id,
        error=detail,
        pipeline=pipeline,
        timings=timings,
    )
    logger.warning(
        "Organize AI unavailable | file=%s | error=%s | timings=%s",
        filepath,
        detail,
        timings,
    )
    return OrganizeFileResult(
        file_id=file_id,
        analysis=FileAnalysisResponse(suggested_names=suggested_names),
        categories=[],
        error=detail,
    )


# ── Dependency Injection ─────────────────────────────────────────────────


def _get_scanner() -> ScannerService:
    return ScannerService()


def _get_rag() -> RagService:
    from app.main import get_rag_service

    return get_rag_service()


def _get_classifier(rag: RagService = Depends(_get_rag)) -> ClassificationService:
    return ClassificationService(rag)


def _get_summary() -> SummaryService:
    return SummaryService()


def _get_calendar_extractor() -> CalendarExtractionService:
    return calendar_extraction_service


def _get_ingest_worker() -> BackgroundIngestWorker:
    from app.main import ingest_worker

    return ingest_worker


def _get_rename() -> RenameService:
    return RenameService()


def _get_history() -> HistoryService:
    return HistoryService()


def _get_system_log() -> SystemLogService:
    return SystemLogService()


def _get_lock_settings_service() -> LockSettingsService:
    return LockSettingsService()


def _normalize_selected_file_name(
    *,
    selected_name: str | None,
    fallback_name: str,
    expected_extension: str,
) -> str:
    """Normalize the user-selected file name without touching the file system."""
    candidate = (selected_name or fallback_name).strip()
    if not candidate:
        raise ValueError("Selected file name must not be empty.")

    pure_name = Path(candidate).name
    if pure_name in {"", ".", ".."}:
        raise ValueError("Selected file name is invalid.")

    if expected_extension and not pure_name.lower().endswith(expected_extension.lower()):
        pure_name = f"{pure_name}{expected_extension}"

    return pure_name


def _build_logged_user_action_result(
    *,
    current_path: str,
    selected_name: str | None,
    destination_dir: str | None,
    expected_extension: str,
) -> dict[str, str | bool | None]:
    """Build the intended rename/move result for history tracking only."""
    source_path = Path(current_path).resolve()
    final_name = _normalize_selected_file_name(
        selected_name=selected_name,
        fallback_name=source_path.name,
        expected_extension=expected_extension,
    )
    target_dir = Path(destination_dir).resolve() if destination_dir else source_path.parent
    final_path = (target_dir / final_name).resolve()
    renamed = final_name != source_path.name
    moved = final_path.parent != source_path.parent

    return {
        "source_path": str(source_path),
        "final_path": str(final_path),
        "file_name": final_name,
        "renamed": renamed,
        "moved": moved,
        "new_path": str(final_path) if renamed or moved else None,
    }


def _build_user_action_history_metadata(
    *,
    file_name: str,
    source_path: str,
    selected_category: ApplySelectedCategoryRequest | None,
    new_path: str | None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "file_name": file_name,
        "source_path": source_path,
    }
    if selected_category:
        metadata["selected_category"] = {
            "id": selected_category.id,
            "name": selected_category.name,
            "score": selected_category.score,
        }
    if new_path:
        metadata["new_path"] = new_path
    return metadata


# ── Route ────────────────────────────────────────────────────────────────


@router.post("/organize", response_model=OrganizeResponse)
async def organize_files(
    body: OrganizeRequest,
    db: AsyncSession = Depends(get_db),
    scanner: ScannerService = Depends(_get_scanner),
    rag: RagService = Depends(_get_rag),
    classifier: ClassificationService = Depends(_get_classifier),
    summary_svc: SummaryService = Depends(_get_summary),
    calendar_svc: CalendarExtractionService = Depends(_get_calendar_extractor),
    rename_svc: RenameService = Depends(_get_rename),
    history_svc: HistoryService = Depends(_get_history),
    system_log_svc: SystemLogService = Depends(_get_system_log),
    lock_svc: LockSettingsService = Depends(_get_lock_settings_service),
    ingest: BackgroundIngestWorker = Depends(_get_ingest_worker),
) -> OrganizeResponse:
    """
    Analyse and classify the given file paths.

    For each file:
      1. Scan metadata (size, hash, extension)
      2. Upsert file record in SQLite
      3. Return cached results if file unchanged (unless ``force=True``)
            4. Parse with fast docling and queue background rich ingestion
            5. Generate AI summary from request-local extracted text
      6. Generate rename suggestion
      7. Score against all active categories
      8. Log history
      9. Return structured result

    It does **not** move, rename, or delete any file.
    """
    logger.info("Organize request — %d file(s), force=%s", len(body.file_paths), body.force)
    lock_settings = await lock_svc.get_settings(db)

    results: dict[str, OrganizeFileResult] = {}

    for filepath in body.file_paths:
        reason = lock_svc.get_lock_reason(filepath, lock_settings)
        if reason:
            logger.info("Organize skipped locked file %s (%s)", filepath, reason)
            results[filepath] = _build_locked_result(filepath, reason)
            continue

        result = await _process_single_file(
            filepath=filepath,
            force=body.force,
            db=db,
            scanner=scanner,
            rag=rag,
            classifier=classifier,
            summary_svc=summary_svc,
            calendar_svc=calendar_svc,
            rename_svc=rename_svc,
            history_svc=history_svc,
            system_log_svc=system_log_svc,
            ingest=ingest,
        )
        results[filepath] = result

    return OrganizeResponse(results=results)


@router.post("/organize/apply", response_model=ApplyOrganizeDecisionResponse)
async def apply_organize_decision(
    body: ApplyOrganizeDecisionRequest,
    db: AsyncSession = Depends(get_db),
    history_svc: HistoryService = Depends(_get_history),
) -> ApplyOrganizeDecisionResponse:
    """Record a user-confirmed rename and/or move after analysis."""
    file_record = await db.get(File, body.file_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="File not found.")

    current_path = getattr(file_record, "current_path")
    current_file = Path(current_path)
    current_extension = file_record.extension or current_file.suffix.lower()

    selected_category = body.selected_category
    selected_category_record: Category | None = None
    destination_dir: str | None = None
    if selected_category:
        selected_category_record = await db.get(Category, selected_category.id)
        if not selected_category_record:
            raise HTTPException(status_code=404, detail="Selected category not found.")
        if not selected_category_record.is_active:
            raise HTTPException(status_code=400, detail="Selected category is disabled.")
        if not selected_category_record.destination_path:
            raise HTTPException(
                status_code=400, detail="Selected category has no destination folder."
            )
        destination_dir = selected_category_record.destination_path

    try:
        result = _build_logged_user_action_result(
            current_path=current_path,
            selected_name=body.selected_name,
            destination_dir=destination_dir,
            expected_extension=current_extension,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    renamed = bool(result["renamed"])
    moved = bool(result["moved"])
    if renamed and moved:
        action = "renamed_moved"
    elif moved:
        action = "moved"
    elif renamed:
        action = "renamed"
    else:
        raise HTTPException(status_code=400, detail="No confirmed file change was applied.")

    final_path = str(result["final_path"])
    file_record.current_path = final_path
    file_record.extension = Path(final_path).suffix.lower() or file_record.extension

    metadata = _build_user_action_history_metadata(
        file_name=str(result["file_name"]),
        source_path=str(result["source_path"]),
        selected_category=selected_category,
        new_path=str(result["new_path"]) if result["new_path"] else None,
    )
    await history_svc.log(
        db=db,
        file_id=file_record.id,
        action=action,
        metadata=metadata,
    )

    return ApplyOrganizeDecisionResponse()


# ── Per-file processing ──────────────────────────────────────────────────


async def _process_single_file(
    filepath: str,
    force: bool,
    db: AsyncSession,
    scanner: ScannerService,
    rag: RagService,
    classifier: ClassificationService,
    summary_svc: SummaryService,
    calendar_svc: CalendarExtractionService,
    rename_svc: RenameService,
    history_svc: HistoryService,
    system_log_svc: SystemLogService,
    ingest: BackgroundIngestWorker,
) -> OrganizeFileResult:
    """
    Process a single file through the optimised AI pipeline.

    Acquires a per-path lock so two concurrent requests for the same file
    never race against each other.
    """
    async with _file_locks[filepath]:
        return await _process_single_file_inner(
            filepath=filepath,
            force=force,
            db=db,
            scanner=scanner,
            rag=rag,
            classifier=classifier,
            summary_svc=summary_svc,
            calendar_svc=calendar_svc,
            rename_svc=rename_svc,
            history_svc=history_svc,
            system_log_svc=system_log_svc,
            ingest=ingest,
        )


async def _process_single_file_inner(
    filepath: str,
    force: bool,
    db: AsyncSession,
    scanner: ScannerService,
    rag: RagService,
    classifier: ClassificationService,
    summary_svc: SummaryService,
    calendar_svc: CalendarExtractionService,
    rename_svc: RenameService,
    history_svc: HistoryService,
    system_log_svc: SystemLogService,
    ingest: BackgroundIngestWorker,
) -> OrganizeFileResult:
    """Core pipeline — assumes caller holds the per-file lock."""
    total_started_at = time.perf_counter()
    timings: dict[str, float] = {}
    pipeline: dict[str, str | bool | None] = {
        "rag_ready": rag.is_ready,
        "ai_status": None,
        "is_new_file": False,
        "file_changed": False,
        "rag_status": None,
        "cached": False,
    }

    # ── Step 1: Scan ─────────────────────────────────────────────────
    step_started_at = time.perf_counter()
    scan = await scanner.scan(filepath)
    timings["scan_ms"] = _elapsed_ms(step_started_at)

    if scan.error:
        timings["total_ms"] = _elapsed_ms(total_started_at)
        await _log_pipeline_issue(
            db=db,
            system_log_svc=system_log_svc,
            level="WARNING",
            event_type="organize_scan_failed",
            message="File scan failed during organize pipeline.",
            filepath=filepath,
            error=scan.error,
            pipeline=pipeline,
            timings=timings,
        )
        logger.warning(
            "Organize failed | file=%s | error=%s | timings=%s",
            filepath,
            scan.error,
            timings,
        )
        return OrganizeFileResult(
            file_id="",
            analysis=FileAnalysisResponse(),
            categories=[],
            error=scan.error,
        )

    # ── Step 2: Upsert file record ───────────────────────────────────
    step_started_at = time.perf_counter()
    file_record = await _get_file_by_current_path(db, scan.original_path)

    is_new_file = False
    previous_hash = file_record.hash if file_record else None
    if file_record:
        # Eagerly load relationships for cache check later
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

    await db.flush()  # assign file_record.id
    file_changed = is_new_file or previous_hash != scan.sha256
    pipeline["is_new_file"] = is_new_file
    pipeline["file_changed"] = file_changed
    timings["file_upsert_ms"] = _elapsed_ms(step_started_at)

    # ── Step 3: Cache check — instant return for unchanged files ─────
    # Three outcomes:
    #   a) full hit  — file unchanged + same categories → return DB cache instantly
    #   b) partial   — file unchanged + analysis inputs changed → re-classify only (fast)
    #   c) full run  — file changed or force=True → run the whole pipeline

    has_cached_analysis = False
    if not is_new_file:
        has_cached_analysis = (
            file_record.analysis is not None and file_record.analysis.summary is not None
        )

    current_analysis_fingerprint = await _get_analysis_fingerprint(db)
    analysis_fingerprint_match = (
        has_cached_analysis
        and file_record.analysis.categories_hash == current_analysis_fingerprint  # type: ignore[union-attr]
    )

    # ── 3a: Full cache hit ────────────────────────────────────────────
    if not file_changed and analysis_fingerprint_match and not force:
        step_started_at = time.perf_counter()
        pipeline["cached"] = True
        pipeline["cache_reason"] = "full_hit"
        pipeline["rag_status"] = "skipped_cached"

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

        timings["cache_lookup_ms"] = _elapsed_ms(step_started_at)

        cached_names: list[str] = []
        if cached_analysis.suggested_names:
            try:
                cached_names = json.loads(cached_analysis.suggested_names)
            except (json.JSONDecodeError, TypeError):
                cached_names = [cached_analysis.suggested_names]

        step_started_at = time.perf_counter()
        history_metadata = _build_history_metadata(
            suggested_names=cached_names,
            scores=score_dicts,
            pipeline=pipeline,
            timings={},
        )
        history_entry = await history_svc.log(
            db=db, file_id=file_record.id, action="organized_cached", metadata=history_metadata
        )
        timings["history_log_ms"] = _elapsed_ms(step_started_at)
        timings["total_ms"] = _elapsed_ms(total_started_at)
        history_metadata["timings"] = timings
        history_entry.metadata_json = json.dumps(history_metadata)
        await db.flush()

        logger.info(
            "Organize cached (full hit) | file=%s | timings=%s", scan.original_path, timings
        )
        return OrganizeFileResult(
            file_id=file_record.id,
            analysis=FileAnalysisResponse(suggested_names=cached_names),
            categories=category_responses,
        )

    # ── 3b: Partial cache — file unchanged but categories changed ─────
    if not file_changed and has_cached_analysis and not force:
        step_started_at = time.perf_counter()
        pipeline["cached"] = "partial"
        pipeline["cache_reason"] = "analysis_inputs_changed"
        pipeline["rag_status"] = "skipped_unchanged"

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
        timings["ai_check_ms"] = _elapsed_ms(ai_check_started_at)
        if ai_errors:
            return await _build_ai_unavailable_result(
                db=db,
                system_log_svc=system_log_svc,
                filepath=scan.original_path,
                file_id=file_record.id,
                suggested_names=cached_names,
                pipeline=pipeline,
                timings=timings,
                total_started_at=total_started_at,
                ai_errors=ai_errors,
                event_type="organize_reclassify_ai_unavailable",
                message="AI capabilities unavailable during organize re-classification.",
            )
        pipeline["ai_status"] = "ready"

        # Re-classify only — reuse cached summary, no LLM summary call
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
                pipeline=pipeline,
                timings=timings,
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
                pipeline=pipeline,
                timings=timings,
            )
        timings["reclassify_ms"] = _elapsed_ms(step_started_at)

        # Persist updated categories_hash
        cached_analysis.categories_hash = current_analysis_fingerprint
        await db.flush()

        category_responses = _build_category_responses(scores)

        step_started_at = time.perf_counter()
        history_metadata = _build_history_metadata(
            suggested_names=cached_names,
            scores=scores,
            pipeline=pipeline,
            timings={},
        )
        history_entry = await history_svc.log(
            db=db,
            file_id=file_record.id,
            action="organized_reclassified",
            metadata=history_metadata,
        )
        timings["history_log_ms"] = _elapsed_ms(step_started_at)
        timings["total_ms"] = _elapsed_ms(total_started_at)
        history_metadata["timings"] = timings
        history_entry.metadata_json = json.dumps(history_metadata)
        await db.flush()

        logger.info(
            "Organize partial cache (re-classified) | file=%s | timings=%s",
            scan.original_path,
            timings,
        )
        return OrganizeFileResult(
            file_id=file_record.id,
            analysis=FileAnalysisResponse(suggested_names=cached_names),
            categories=category_responses,
        )

    # ── Step 4: Live AI capability check ─────────────────────────────
    step_started_at = time.perf_counter()
    ai_errors = await _collect_organize_ai_errors(rag)
    timings["ai_check_ms"] = _elapsed_ms(step_started_at)
    if ai_errors:
        return await _build_ai_unavailable_result(
            db=db,
            system_log_svc=system_log_svc,
            filepath=scan.original_path,
            file_id=file_record.id,
            suggested_names=[],
            pipeline=pipeline,
            timings=timings,
            total_started_at=total_started_at,
            ai_errors=ai_errors,
            event_type="organize_ai_unavailable",
            message="AI capabilities unavailable during organize pipeline.",
        )
    pipeline["ai_status"] = "ready"

    # ── Step 5: Enqueue background RAG ingestion (non-blocking) ──────
    step_started_at = time.perf_counter()
    prepared_ingest = None
    if not rag.is_ready:
        pipeline["rag_status"] = "not_ready"
    elif not file_changed and not force:
        pipeline["rag_status"] = "skipped_unchanged"
    else:
        prepared_ingest = await ingest.enqueue(filepath=scan.original_path)
        pipeline["rag_status"] = prepared_ingest.ingest_status
    timings["rag_enqueue_ms"] = _elapsed_ms(step_started_at)

    # ── Step 6: Generate summary + extract calendar event in parallel ──
    step_started_at = time.perf_counter()
    summary_text: str | None = None
    calendar_event: CalendarEventDto | None = None
    try:
        extracted_text = prepared_ingest.extracted_text if prepared_ingest else None
        summary_result, calendar_result = await asyncio.gather(
            summary_svc.summarise(
                scan.original_path,
                extracted_text=extracted_text,
            ),
            calendar_svc.extract(
                scan.original_path,
                extracted_text=extracted_text,
            ),
            return_exceptions=True,
        )

        if isinstance(summary_result, AiCapabilityUnavailableError):
            raise summary_result
        if isinstance(summary_result, Exception):
            raise summary_result
        summary_text = summary_result

        if isinstance(calendar_result, Exception):
            # Calendar extraction failures are non-fatal — log and continue.
            logger.warning(
                "Calendar extraction failed for %s: %s",
                scan.original_path,
                calendar_result,
            )
            calendar_event = None
        else:
            calendar_event = calendar_result
    except AiCapabilityUnavailableError as exc:
        return await _build_ai_unavailable_result(
            db=db,
            system_log_svc=system_log_svc,
            filepath=scan.original_path,
            file_id=file_record.id,
            suggested_names=[],
            pipeline=pipeline,
            timings=timings,
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
            pipeline=pipeline,
            timings=timings,
        )
    timings["summary_ms"] = _elapsed_ms(step_started_at)

    # ── Step 7: Generate rename suggestion ───────────────────────────
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
            pipeline=pipeline,
            timings=timings,
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
            pipeline=pipeline,
            timings=timings,
        )
    timings["rename_ms"] = _elapsed_ms(step_started_at)

    # ── Step 8: Store analysis (robust upsert) ──────────────────────
    step_started_at = time.perf_counter()
    # Store the analysis fingerprint so cache invalidation follows category or parser changes
    names_json = json.dumps(suggested_names) if suggested_names else None
    calendar_event_json = (
        calendar_event.model_dump_json() if calendar_event is not None else None
    )
    if not is_new_file and file_record.analysis is not None:
        # Update existing — relationships were loaded via refresh in Step 2
        file_record.analysis.summary = summary_text
        file_record.analysis.suggested_names = names_json
        file_record.analysis.categories_hash = current_analysis_fingerprint
        if calendar_event_json is not None:
            file_record.analysis.calendar_event_json = calendar_event_json
    else:
        analysis = FileAnalysis(
            file_id=file_record.id,
            summary=summary_text,
            suggested_names=names_json,
            categories_hash=current_analysis_fingerprint,
            calendar_event_json=calendar_event_json,
        )
        db.add(analysis)

    await db.flush()
    timings["analysis_save_ms"] = _elapsed_ms(step_started_at)

    # ── Step 8b: Record detected calendar event (dedup by file_id) ──
    if calendar_event is not None:
        existing_event = await db.execute(
            select(DetectedCalendarEvent).where(
                DetectedCalendarEvent.file_id == file_record.id
            )
        )
        existing = existing_event.scalar_one_or_none()
        if existing is None:
            db.add(
                DetectedCalendarEvent(
                    file_id=file_record.id,
                    event_json=calendar_event_json or "{}",
                    status="pending",
                )
            )
            await db.flush()
            await history_svc.log(
                db=db,
                file_id=file_record.id,
                action="calendar_event_detected",
                metadata={
                    "title": calendar_event.title,
                    "start_iso": calendar_event.start_iso,
                    "all_day": calendar_event.all_day,
                    "confidence": calendar_event.confidence,
                },
            )

    # ── Step 9: Classify with summary-enriched embedding ─────────────
    step_started_at = time.perf_counter()
    scores: list[dict] = []
    try:
        # Generate embedding with summary for richer semantic signal
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
            pipeline=pipeline,
            timings=timings,
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
            pipeline=pipeline,
            timings=timings,
        )
    timings["classify_ms"] = _elapsed_ms(step_started_at)

    category_responses = _build_category_responses(scores)

    # ── Step 10: Log history ─────────────────────────────────────────
    history_metadata = _build_history_metadata(
        suggested_names=suggested_names,
        scores=scores,
        pipeline=pipeline,
        timings={},
    )
    step_started_at = time.perf_counter()
    history_entry = await history_svc.log(
        db=db,
        file_id=file_record.id,
        action="organized",
        metadata=history_metadata,
    )
    timings["history_log_ms"] = _elapsed_ms(step_started_at)
    timings["total_ms"] = _elapsed_ms(total_started_at)
    history_metadata["timings"] = timings
    history_entry.metadata_json = json.dumps(history_metadata)
    await db.flush()

    logger.info(
        "Organize complete | file=%s | new=%s | changed=%s | rag=%s | timings=%s",
        scan.original_path,
        is_new_file,
        file_changed,
        pipeline["rag_status"],
        timings,
    )

    return OrganizeFileResult(
        file_id=file_record.id,
        analysis=FileAnalysisResponse(
            suggested_names=suggested_names,
        ),
        categories=category_responses,
    )
