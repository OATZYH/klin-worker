"""
Summary API router (V3).

POST /api/summary — generate a combined summary for the given files.

Uses the existing SummaryService (vision model for images, LLM for
text/documents) and concatenates per-file summaries into a single
combined response.

Caching:
  • If the file has already been processed by organize, its summary is
    stored in file_analysis.summary and returned instantly (cache hit).
  • Pass ``force=true`` to bypass the cache and regenerate.
  • Freshly generated summaries are persisted back to file_analysis so
    subsequent calls are always fast.
"""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.ai_exceptions import (
    AiCapabilityUnavailableError,
    to_service_unavailable_http_exception,
)
from app.db.models import File, FileAnalysis
from app.db.session import get_db
from app.services.llm_client import llm_client
from app.services.rag_service import RagService
from app.services.summary_service import SummaryService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["summary"])


# ── Request / Response models ────────────────────────────────────────────


class SummaryRequest(BaseModel):
    """POST /api/summary request body."""

    file_paths: list[str] = Field(
        ...,
        min_length=1,
        description="Absolute file paths to summarise.",
    )
    force: bool = Field(
        default=False,
        description="Force regeneration even when a cached summary exists.",
    )


class SummaryResponse(BaseModel):
    """POST /api/summary response body."""

    summary: str


# ── Dependency Injection ─────────────────────────────────────────────────


def _get_rag() -> RagService:
    from app.main import get_rag_service

    return get_rag_service()


def _get_summary(rag: RagService = Depends(_get_rag)) -> SummaryService:
    return SummaryService(rag)


# ── Cache helpers ─────────────────────────────────────────────────────────


async def _get_cached_summary(db: AsyncSession, file_path: str) -> str | None:
    """Return the cached summary from file_analysis if it exists."""
    current_path_col = getattr(File, "current_path")
    result = await db.execute(select(File).where(current_path_col == file_path))
    file_record = result.scalar_one_or_none()
    if not file_record:
        return None
    await db.refresh(file_record, attribute_names=["analysis"])
    if file_record.analysis and file_record.analysis.summary:
        return file_record.analysis.summary
    return None


async def _persist_summary(db: AsyncSession, file_path: str, summary: str) -> None:
    """Write the generated summary back to file_analysis for future cache hits."""
    current_path_col = getattr(File, "current_path")
    result = await db.execute(select(File).where(current_path_col == file_path))
    file_record = result.scalar_one_or_none()
    if not file_record:
        return
    await db.refresh(file_record, attribute_names=["analysis"])
    if file_record.analysis:
        file_record.analysis.summary = summary
    else:
        db.add(FileAnalysis(file_id=file_record.id, summary=summary))


# ── Route ────────────────────────────────────────────────────────────────


@router.post("/summary", response_model=SummaryResponse)
async def summarise_files(
    body: SummaryRequest,
    db: AsyncSession = Depends(get_db),
    summary_svc: SummaryService = Depends(_get_summary),
) -> SummaryResponse:
    """
    Generate a combined summary from the provided files.

    Cache-first: if the file was already processed by organize, its summary
    is returned instantly from file_analysis.  Pass ``force=true`` to
    regenerate.  Freshly generated summaries are persisted back to the DB.
    """
    logger.info("Summary request — %d file(s), force=%s", len(body.file_paths), body.force)

    try:
        await llm_client.ensure_general_available()
    except AiCapabilityUnavailableError as exc:
        raise to_service_unavailable_http_exception(exc) from exc

    summaries: list[str] = []
    for fp in body.file_paths:
        # ── Cache check ───────────────────────────────────────────────
        if not body.force:
            cached = await _get_cached_summary(db, fp)
            if cached:
                logger.info("Summary cache hit | file=%s", fp)
                summaries.append(cached)
                continue

        # ── Generate via LLM ─────────────────────────────────────────
        try:
            text = await summary_svc.summarise(fp)
            if text:
                await _persist_summary(db, fp, text)
                summaries.append(text)
        except AiCapabilityUnavailableError as exc:
            raise to_service_unavailable_http_exception(exc) from exc
        except Exception as exc:
            logger.error("Summary failed for %s: %s", fp, exc)

    combined = "\n\n".join(summaries) if summaries else "No summary could be generated."
    return SummaryResponse(summary=combined)
