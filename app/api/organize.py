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

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import Category, File
from app.db.session import get_db
from app.models.request import (
    ApplyOrganizeDecisionRequest,
    ApplySelectedCategoryRequest,
    OrganizeRequest,
)
from app.observability.tracing import update_current_span
from app.models.response import (
    ApplyOrganizeDecisionResponse,
    OrganizeFileResult,
    OrganizeResponse,
)
from app.services.organize.background_ingest import BackgroundIngestWorker
from app.services.organize.classification_service import ClassificationService
from app.services.history_service import HistoryService
from app.services.ai.rag_service import RagService
from app.services.organize.organize_pipeline import build_locked_result, process_single_file
from app.services.organize.rename_service import RenameService
from app.services.organize.schedule_extraction_service import ScheduleExtractionService
from app.services.organize.scanner_service import ScannerService
from app.services.summary.summary_service import SummaryService
from app.services.system_log_service import SystemLogService
from app.services.settings.lock_settings_service import LockSettingsService

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


def _get_summary() -> SummaryService:
    return SummaryService()


def _get_ingest_worker() -> BackgroundIngestWorker:
    from app.main import ingest_worker

    return ingest_worker


def _get_rename() -> RenameService:
    return RenameService()


def _get_schedule_extraction() -> ScheduleExtractionService:
    return ScheduleExtractionService()


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
    rename_svc: RenameService = Depends(_get_rename),
    schedule_svc: ScheduleExtractionService = Depends(_get_schedule_extraction),
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
    update_current_span(
        input={"file_paths": body.file_paths, "force": body.force},
        metadata={"feature": "organize", "file_count": len(body.file_paths)},
    )
    lock_settings = await lock_svc.get_settings(db)

    results: dict[str, OrganizeFileResult] = {}

    for filepath in body.file_paths:
        reason = lock_svc.get_lock_reason(filepath, lock_settings)
        if reason:
            logger.info("Organize skipped locked file %s (%s)", filepath, reason)
            results[filepath] = build_locked_result(filepath, reason)
            continue

        result = await process_single_file(
            filepath=filepath,
            force=body.force,
            db=db,
            scanner=scanner,
            rag=rag,
            classifier=classifier,
            summary_svc=summary_svc,
            rename_svc=rename_svc,
            schedule_svc=schedule_svc,
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
