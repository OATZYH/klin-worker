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

import logging

from fastapi import APIRouter, Depends
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

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

    # ── Step 1: Scan ─────────────────────────────────────────────────
    scan = await scanner.scan(filepath)
    if scan.error:
        return OrganizeFileResult(
            filepath=filepath,
            file_id="",
            analysis=FileAnalysisResponse(),
            categories=[],
            error=scan.error,
        )

    # ── Step 2: Upsert file record ───────────────────────────────────
    existing = await db.execute(
        select(File).where(File.original_path == scan.original_path)
    )
    file_record = existing.scalar_one_or_none()

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

    await db.flush()  # assign file_record.id

    # ── Step 3: Ingest into RAG ──────────────────────────────────────
    if rag.is_ready:
        await rag.ingest(scan.original_path)

    # ── Step 4: Generate summary ─────────────────────────────────────
    summary_text = await summary_svc.summarise(scan.original_path)

    # ── Step 5: Generate rename suggestion ───────────────────────────
    suggested_name = await rename_svc.suggest_name(
        original_name=scan.file_name,
        extension=scan.extension,
        summary=summary_text,
    )

    # ── Step 6: Store analysis ───────────────────────────────────────
    if file_record.analysis:
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

    # ── Step 7: Classify against categories ──────────────────────────
    scores = await classifier.classify(
        file_id=file_record.id,
        file_path=scan.original_path,
        db=db,
    )

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
    if scores:
        top = scores[0]
        # Look up destination path
        from app.db.models import Category

        cat = await db.get(Category, top["category_id"])
        top_category = TopCategoryResponse(
            category_id=top["category_id"],
            name=top["name"],
            score=top["score"],
            destination_path=cat.destination_path if cat else None,
        )

    # ── Step 8: Log history ──────────────────────────────────────────
    await history_svc.log(
        db=db,
        file_id=file_record.id,
        action="categorized",
        metadata={
            "top_category": top_category.name if top_category else None,
            "confidence": top_category.score if top_category else 0.0,
            "scores_count": len(scores),
        },
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
