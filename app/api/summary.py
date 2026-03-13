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
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.ai_exceptions import (
    AiCapabilityUnavailableError,
    to_service_unavailable_http_exception,
)
from app.db.session import get_db
from app.services.ai.rag_service import RagService
from app.services.ai.summary_service import SummaryService
from app.services.summary_workflow_service import SummaryWorkflowService

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


def _get_summary_workflow(
    summary_svc: SummaryService = Depends(_get_summary),
) -> SummaryWorkflowService:
    return SummaryWorkflowService(summary_svc)


# ── Route ────────────────────────────────────────────────────────────────


@router.post("/summary", response_model=SummaryResponse)
async def summarise_files(
    body: SummaryRequest,
    db: AsyncSession = Depends(get_db),
    workflow: SummaryWorkflowService = Depends(_get_summary_workflow),
) -> SummaryResponse:
    """
    Generate a combined summary from the provided files.

    Cache-first: if the file was already processed by organize, its summary
    is returned instantly from file_analysis.  Pass ``force=true`` to
    regenerate.  Freshly generated summaries are persisted back to the DB.
    """
    logger.info("Summary request — %d file(s), force=%s", len(body.file_paths), body.force)

    for fp in body.file_paths:
        logger.debug("Queued summary request item | file=%s", fp)

    try:
        combined = await workflow.summarise_files(
            file_paths=body.file_paths,
            force=body.force,
            db=db,
        )
    except AiCapabilityUnavailableError as exc:
        raise to_service_unavailable_http_exception(exc) from exc
    except Exception as exc:
        logger.error("Summary request failed: %s", exc)
        combined = "No summary could be generated."

    return SummaryResponse(summary=combined)
