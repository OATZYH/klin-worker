"""
Organize API router.

POST /api/organize — analyse files and return an action plan.
No file moving, renaming, or deletion.
"""

import logging

from fastapi import APIRouter, Depends

from app.models.request import OrganizeRequest
from app.models.response import (
    FileAnalysisResult,
    OrganizeResponse,
    OrganizeSummary,
)
from app.services.rag_service import RagService
from app.services.scanner_service import ScannerService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["organize"])


# ── Dependency Injection ─────────────────────────────────────────────────

def get_scanner() -> ScannerService:
    return ScannerService()


def get_rag() -> RagService:
    """Injected from app state (initialised at startup)."""
    from app.main import get_rag_service

    return get_rag_service()


# ── Route ────────────────────────────────────────────────────────────────

@router.post("/organize", response_model=OrganizeResponse)
async def organize_files(
    body: OrganizeRequest,
    scanner: ScannerService = Depends(get_scanner),
    rag: RagService = Depends(get_rag),
) -> OrganizeResponse:
    """
    Analyse the given file paths and return a structured action plan.

    The endpoint:
      1. Scans each file for metadata (size, hash, extension)
      2. Ingests valid files into RAG-Anything
      3. Optionally checks for semantic duplicates
      4. Returns per-file analysis + summary

    It does **not** move, rename, or delete any file.
    """
    logger.info("Organize request — %d file(s)", len(body.organize))

    # Step 1: Scan all files
    scan_results = await scanner.scan_many(body.organize)

    # Step 2: Process each file
    analysis_results: list[FileAnalysisResult] = []
    duplicates_found = 0
    rename_suggestions = 0
    errors = 0

    for scan in scan_results:
        # If scan failed, short-circuit
        if scan.error:
            analysis_results.append(
                FileAnalysisResult(
                    original_path=scan.original_path,
                    status="error",
                    confidence=0.0,
                    metadata=scan,
                )
            )
            errors += 1
            continue

        # Step 2a: Ingest into RAG (non-blocking for analysis)
        ingested = False
        if rag.is_ready:
            ingested = await rag.ingest(scan.original_path)

        # Step 2b: Duplicate check (if enabled)
        duplicate_of = None
        if body.rules.check_dup and rag.is_ready:
            dups = await rag.find_duplicates(scan.original_path)
            if dups:
                duplicate_of = dups[0].get("path")
                duplicates_found += 1

        # Build result
        status = "duplicate" if duplicate_of else "ok"
        confidence = 0.95 if ingested else 0.5

        analysis_results.append(
            FileAnalysisResult(
                original_path=scan.original_path,
                status=status,
                duplicate_of=duplicate_of,
                suggested_name=None,  # future: AI-generated name
                suggested_category=None,  # future: semantic category
                confidence=confidence,
                metadata=scan,
            )
        )

    # Step 3: Build summary
    scanned_ok = len(scan_results) - errors
    summary = OrganizeSummary(
        total_files=len(body.organize),
        scanned_ok=scanned_ok,
        duplicates_found=duplicates_found,
        rename_suggestions=rename_suggestions,
        errors=errors,
    )

    return OrganizeResponse(summary=summary, files=analysis_results)
