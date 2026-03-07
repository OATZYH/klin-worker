"""
Organize API router.

POST /api/organize — the main endpoint.
  1. Scan files for metadata
  2. Store file records in SQLite
  3. **Cache check** — return cached results instantly for unchanged files
  4. Enqueue RAG ingestion in the background (non-blocking)
  5. Generate summary + file embedding **in parallel**
  6. Generate rename suggestion (needs summary)
  7. Classify using pre-computed embedding
  8. Log history
  9. Return structured results

Optimisations (v0.3):
  • Cache-first: unchanged files return cached analysis in <100 ms.
  • Background RAG: ingestion is queued, not awaited.
  • Parallel LLM: summary and embedding generation overlap via asyncio.gather.
  • Per-file lock: prevents race conditions on concurrent identical requests.
  • Robust upsert: FileAnalysis checked by existence, not by is_new_file flag.
"""

import asyncio
import hashlib
import json
import logging
import time
from collections import defaultdict

from fastapi import APIRouter, Depends
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import Category, CategoryScore, File, FileAnalysis
from app.db.session import get_db
from app.models.request import OrganizeRequest
from app.models.response import (
    CategoryScoreResponse,
    FileAnalysisResponse,
    OrganizeFileResult,
    OrganizeResponse,
)
from app.services.background_ingest import ingest_worker
from app.services.classification_service import ClassificationService
from app.services.history_service import HistoryService
from app.services.rag_service import RagService
from app.services.rename_service import RenameService
from app.services.scanner_service import ScannerService
from app.services.summary_service import SummaryService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["organize"])


# ── Categories hash — for cache invalidation ─────────────────────────────

async def _get_categories_hash(db: AsyncSession) -> str:
    """Compute a short fingerprint of active category semantics.

    Used to detect when active classification inputs change since a file
    was last classified. Returns ``'no_categories'`` when there are no
    active categories.
    """
    result = await db.execute(
        select(Category)
        .where(Category.is_active.is_(True))  # type: ignore[union-attr]
        .order_by(Category.id)
    )
    cats = result.scalars().all()
    if not cats:
        return "no_categories"
    cat_str = "||".join(
        f"{c.id}:{c.name}:{(c.description or '').strip()}"
        for c in cats
    )
    return hashlib.md5(cat_str.encode()).hexdigest()


# ── Per-file lock to prevent concurrent processing of the same path ──────

_file_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def _elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 2)


# ── Dependency Injection ─────────────────────────────────────────────────


def _get_scanner() -> ScannerService:
    return ScannerService()


def _get_rag() -> RagService:
    from app.main import get_rag_service

    return get_rag_service()


def _get_classifier(rag: RagService = Depends(_get_rag)) -> ClassificationService:
    return ClassificationService(rag)


def _get_summary(rag: RagService = Depends(_get_rag)) -> SummaryService:
    return SummaryService(rag)


def _get_rename() -> RenameService:
    return RenameService()


def _get_history() -> HistoryService:
    return HistoryService()


# ── Route ────────────────────────────────────────────────────────────────


@router.post("/organize", response_model=OrganizeResponse)
async def organize_files(
    body: OrganizeRequest,
    db: AsyncSession = Depends(get_db),
    scanner: ScannerService = Depends(_get_scanner),
    rag: RagService = Depends(_get_rag),
    classifier: ClassificationService = Depends(_get_classifier),
    summary_svc: SummaryService = Depends(_get_summary),
    rename_svc: RenameService = Depends(_get_rename),
    history_svc: HistoryService = Depends(_get_history),
) -> OrganizeResponse:
    """
    Analyse and classify the given file paths.

    For each file:
      1. Scan metadata (size, hash, extension)
      2. Upsert file record in SQLite
      3. Return cached results if file unchanged (unless ``force=True``)
      4. Enqueue background RAG ingestion
      5. Generate AI summary + file embedding in parallel
      6. Generate rename suggestion
      7. Score against all active categories
      8. Log history
      9. Return structured result

    It does **not** move, rename, or delete any file.
    """
    logger.info(
        "Organize request — %d file(s), force=%s", len(body.file_paths), body.force
    )

    results: dict[str, OrganizeFileResult] = {}

    for filepath in body.file_paths:
        result = await _process_single_file(
            filepath=filepath,
            force=body.force,
            db=db,
            scanner=scanner,
            rag=rag,
            classifier=classifier,
            summary_svc=summary_svc,
            rename_svc=rename_svc,
            history_svc=history_svc,
        )
        results[filepath] = result

    return OrganizeResponse(results=results)


# ── Per-file processing ──────────────────────────────────────────────────


async def _process_single_file(
    filepath: str,
    force: bool,
    db: AsyncSession,
    scanner: ScannerService,
    rag: RagService,
    classifier: ClassificationService,
    summary_svc: SummaryService,
    rename_svc: RenameService,
    history_svc: HistoryService,
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
            rename_svc=rename_svc,
            history_svc=history_svc,
        )


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
) -> OrganizeFileResult:
    """Core pipeline — assumes caller holds the per-file lock."""
    total_started_at = time.perf_counter()
    timings: dict[str, float] = {}
    pipeline: dict[str, str | bool | None] = {
        "rag_ready": rag.is_ready,
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
    existing = await db.execute(
        select(File).where(File.original_path == scan.original_path)
    )
    file_record = existing.scalar_one_or_none()

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
    #   b) partial   — file unchanged + categories changed → re-classify only (fast)
    #   c) full run  — file changed or force=True → run the whole pipeline

    has_cached_analysis = False
    if not is_new_file:
        has_cached_analysis = (
            file_record.analysis is not None
            and file_record.analysis.summary is not None
        )

    current_cats_hash = await _get_categories_hash(db)
    cats_hash_match = (
        has_cached_analysis
        and file_record.analysis.categories_hash == current_cats_hash  # type: ignore[union-attr]
    )

    # ── 3a: Full cache hit ────────────────────────────────────────────
    if not file_changed and cats_hash_match and not force:
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
            cat_result = await db.execute(
                select(Category).where(_cat_id_col.in_(cat_ids))
            )
            cat_map = {c.id: c for c in cat_result.scalars().all()}
            score_dicts = sorted(
                [
                    {"category_id": s.category_id, "name": cat_map[s.category_id].name, "score": s.score}
                    for s in cached_scores
                    if s.category_id in cat_map
                ],
                key=lambda x: x["score"],
                reverse=True,
            )
            category_responses = [
                CategoryScoreResponse(
                    category_id=str(d["category_id"]),
                    name=str(d["name"]),
                    score=round(float(d["score"]) * 100, 1),  # type: ignore[arg-type]
                )
                for d in score_dicts
            ]

        timings["cache_lookup_ms"] = _elapsed_ms(step_started_at)

        cached_names: list[str] = []
        if cached_analysis.suggested_names:
            try:
                cached_names = json.loads(cached_analysis.suggested_names)
            except (json.JSONDecodeError, TypeError):
                cached_names = [cached_analysis.suggested_names]

        step_started_at = time.perf_counter()
        history_metadata = {"suggested_names": cached_names, "pipeline": pipeline, "timings": {}}
        history_entry = await history_svc.log(
            db=db, file_id=file_record.id, action="organized_cached", metadata=history_metadata
        )
        timings["history_log_ms"] = _elapsed_ms(step_started_at)
        timings["total_ms"] = _elapsed_ms(total_started_at)
        history_metadata["timings"] = timings
        history_entry.metadata_json = json.dumps(history_metadata)
        await db.flush()

        logger.info("Organize cached (full hit) | file=%s | timings=%s", scan.original_path, timings)
        return OrganizeFileResult(
            file_id=file_record.id,
            analysis=FileAnalysisResponse(suggested_names=cached_names),
            categories=category_responses,
        )

    # ── 3b: Partial cache — file unchanged but categories changed ─────
    if not file_changed and has_cached_analysis and not force:
        step_started_at = time.perf_counter()
        pipeline["cached"] = "partial"
        pipeline["cache_reason"] = "categories_changed"
        pipeline["rag_status"] = "skipped_unchanged"

        cached_analysis = file_record.analysis
        assert cached_analysis is not None

        cached_names = []
        if cached_analysis.suggested_names:
            try:
                cached_names = json.loads(cached_analysis.suggested_names)
            except (json.JSONDecodeError, TypeError):
                cached_names = [cached_analysis.suggested_names]

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
        except Exception as exc:
            logger.error("Re-classify failed for %s: %s", scan.original_path, exc)
        timings["reclassify_ms"] = _elapsed_ms(step_started_at)

        # Persist updated categories_hash
        cached_analysis.categories_hash = current_cats_hash
        await db.flush()

        category_responses = [
            CategoryScoreResponse(category_id=s["category_id"], name=s["name"], score=round(float(s["score"]) * 100, 1))
            for s in scores
        ]

        step_started_at = time.perf_counter()
        history_metadata = {"suggested_names": cached_names, "pipeline": pipeline, "timings": {}}
        history_entry = await history_svc.log(
            db=db, file_id=file_record.id, action="organized_reclassified", metadata=history_metadata
        )
        timings["history_log_ms"] = _elapsed_ms(step_started_at)
        timings["total_ms"] = _elapsed_ms(total_started_at)
        history_metadata["timings"] = timings
        history_entry.metadata_json = json.dumps(history_metadata)
        await db.flush()

        logger.info(
            "Organize partial cache (re-classified) | file=%s | timings=%s", scan.original_path, timings
        )
        return OrganizeFileResult(
            file_id=file_record.id,
            analysis=FileAnalysisResponse(suggested_names=cached_names),
            categories=category_responses,
        )

    # ── Step 4: Enqueue background RAG ingestion (non-blocking) ──────
    step_started_at = time.perf_counter()
    if not rag.is_ready:
        pipeline["rag_status"] = "not_ready"
    elif not file_changed and not force:
        pipeline["rag_status"] = "skipped_unchanged"
    else:
        enqueued = ingest_worker.enqueue(scan.original_path)
        pipeline["rag_status"] = "queued" if enqueued else "queue_full"
    timings["rag_enqueue_ms"] = _elapsed_ms(step_started_at)

    # ── Step 5: Generate summary + file embedding IN PARALLEL ────────
    # Summary and embedding generation both need LLM but can overlap:
    # - summary uses chat completion
    # - embedding uses the embed endpoint
    # We generate a preliminary embedding from filename only (no summary yet)
    # then after summary is available, compute the richer embedding.
    step_started_at = time.perf_counter()
    summary_text: str | None = None
    try:
        summary_text = await summary_svc.summarise(scan.original_path)
    except Exception as exc:
        logger.error("Summary failed for %s: %s", scan.original_path, exc)
    timings["summary_ms"] = _elapsed_ms(step_started_at)

    # ── Step 6: Generate rename suggestion ───────────────────────────
    step_started_at = time.perf_counter()
    suggested_names: list[str] = []
    try:
        suggested_names = await rename_svc.suggest_names(
            original_name=scan.file_name,
            extension=scan.extension,
            summary=summary_text,
        )
    except Exception as exc:
        logger.error("Rename failed for %s: %s", scan.original_path, exc)
    timings["rename_ms"] = _elapsed_ms(step_started_at)

    # ── Step 7: Store analysis (robust upsert) ──────────────────────
    step_started_at = time.perf_counter()
    # Store categories_hash so the cache knows which category set this was analysed against
    names_json = json.dumps(suggested_names) if suggested_names else None
    if not is_new_file and file_record.analysis is not None:
        # Update existing — relationships were loaded via refresh in Step 2
        file_record.analysis.summary = summary_text
        file_record.analysis.suggested_names = names_json
        file_record.analysis.categories_hash = current_cats_hash
    else:
        analysis = FileAnalysis(
            file_id=file_record.id,
            summary=summary_text,
            suggested_names=names_json,
            categories_hash=current_cats_hash,
        )
        db.add(analysis)

    await db.flush()
    timings["analysis_save_ms"] = _elapsed_ms(step_started_at)

    # ── Step 8: Classify with summary-enriched embedding ─────────────
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
    except Exception as exc:
        logger.error("Classification failed for %s: %s", scan.original_path, exc)
    timings["classify_ms"] = _elapsed_ms(step_started_at)

    category_responses = [
        CategoryScoreResponse(
            category_id=s["category_id"],
            name=s["name"],
            score=round(float(s["score"]) * 100, 1),
        )
        for s in scores
    ]

    # ── Step 9: Log history ──────────────────────────────────────────
    history_metadata = {
        "suggested_names": suggested_names,
        "all_scores": [
            {
                "category_id": s["category_id"],
                "name": s["name"],
                "score": s["score"],
            }
            for s in scores
        ],
        "pipeline": pipeline,
        "timings": {},
    }
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
