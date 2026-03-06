"""
Organize API router.

POST /api/organize — the main endpoint.
  1. Scan files for metadata
  2. Store file records in SQLite
  3. Ingest into RAG
  4. Generate summary + rename suggestion
  5. Classify against user categories
  6. Log history
  7. Return structured results
"""

import json
import logging
import time

from fastapi import APIRouter, Depends
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import File, FileAnalysis
from app.db.session import get_db
from app.models.request import OrganizeRequest
from app.models.response import (
    CategoryScoreResponse,
    FileAnalysisResponse,
    OrganizeFileResult,
    OrganizeResponse,
    TopCategoryResponse,
)
from app.services.classification_service import ClassificationService
from app.services.history_service import HistoryService
from app.services.rag_service import RagService
from app.services.rename_service import RenameService
from app.services.scanner_service import ScannerService
from app.services.summary_service import SummaryService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["organize"])


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
      3. Ingest into RAG-Anything
      4. Generate AI summary
      5. Generate rename suggestion
      6. Score against all active categories
      7. Log history
      8. Return structured result

    It does **not** move, rename, or delete any file.
    """
    logger.info("Organize request — %d file(s)", len(body.filepaths))

    results: list[OrganizeFileResult] = []

    for filepath in body.filepaths:
        result = await _process_single_file(
            filepath=filepath,
            db=db,
            scanner=scanner,
            rag=rag,
            classifier=classifier,
            summary_svc=summary_svc,
            rename_svc=rename_svc,
            history_svc=history_svc,
        )
        results.append(result)

    return OrganizeResponse(results=results)


# ── Per-file processing ──────────────────────────────────────────────────


async def _process_single_file(
    filepath: str,
    db: AsyncSession,
    scanner: ScannerService,
    rag: RagService,
    classifier: ClassificationService,
    summary_svc: SummaryService,
    rename_svc: RenameService,
    history_svc: HistoryService,
) -> OrganizeFileResult:
    """Process a single file through the full AI pipeline."""
    total_started_at = time.perf_counter()
    timings: dict[str, float] = {}
    pipeline: dict[str, str | bool | None] = {
        "rag_ready": rag.is_ready,
        "is_new_file": False,
        "file_changed": False,
        "rag_status": None,
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
            filepath=filepath,
            file_id="",
            analysis=FileAnalysisResponse(),
            categories=[],
            error=scan.error,
        )

    # ── Step 2: Upsert file record ───────────────────────────────────
    step_started_at = time.perf_counter()
    existing = await db.execute(
        select(File)
        .options(selectinload(File.analysis))
        .where(File.original_path == scan.original_path)
    )
    file_record = existing.scalar_one_or_none()

    is_new_file = False
    previous_hash = file_record.hash if file_record else None
    if file_record:
        # Update hash/size if file changed
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

    # ── Step 3: Ingest into RAG ──────────────────────────────────────
    step_started_at = time.perf_counter()
    if not rag.is_ready:
        pipeline["rag_status"] = "not_ready"
    elif not file_changed:
        pipeline["rag_status"] = "skipped_unchanged"
    else:
        ingest_ok = await rag.ingest(scan.original_path)
        pipeline["rag_status"] = "ingested" if ingest_ok else "failed"
    timings["rag_ingest_ms"] = _elapsed_ms(step_started_at)

    # ── Step 4: Generate summary ─────────────────────────────────────
    step_started_at = time.perf_counter()
    summary_text = await summary_svc.summarise(scan.original_path)
    timings["summary_ms"] = _elapsed_ms(step_started_at)

    # ── Step 5: Generate rename suggestion ───────────────────────────
    step_started_at = time.perf_counter()
    suggested_name = await rename_svc.suggest_name(
        original_name=scan.file_name,
        extension=scan.extension,
        summary=summary_text,
    )
    timings["rename_ms"] = _elapsed_ms(step_started_at)

    # ── Step 6: Store analysis ───────────────────────────────────────
    step_started_at = time.perf_counter()
    if not is_new_file and file_record.analysis:
        file_record.analysis.summary = summary_text
        file_record.analysis.suggested_name = suggested_name
    else:
        analysis = FileAnalysis(
            file_id=file_record.id,
            summary=summary_text,
            suggested_name=suggested_name,
        )
        db.add(analysis)

    await db.flush()
    timings["analysis_save_ms"] = _elapsed_ms(step_started_at)

    # ── Step 7: Classify against categories ──────────────────────────
    step_started_at = time.perf_counter()
    scores = await classifier.classify(
        file_id=file_record.id,
        file_path=scan.original_path,
        db=db,
        summary=summary_text,
    )
    timings["classify_ms"] = _elapsed_ms(step_started_at)

    category_responses = [
        CategoryScoreResponse(
            category_id=s["category_id"],
            name=s["name"],
            score=s["score"],
        )
        for s in scores
    ]

    # Determine top category
    top_category = None
    step_started_at = time.perf_counter()
    if scores:
        top = scores[0]
        top_category = TopCategoryResponse(
            category_id=top["category_id"],
            name=top["name"],
            score=top["score"],
            destination_path=top.get("destination_path"),
        )
    timings["top_category_lookup_ms"] = _elapsed_ms(step_started_at)

    # ── Step 8: Log history ──────────────────────────────────────────
    history_metadata = {
        "top_category": top_category.name if top_category else None,
        "top_score": top_category.score if top_category else 0.0,
        "suggested_name": suggested_name,
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
        filepath=scan.original_path,
        file_id=file_record.id,
        analysis=FileAnalysisResponse(
            summary=summary_text,
            suggested_name=suggested_name,
        ),
        categories=category_responses,
        top_category=top_category,
    )
