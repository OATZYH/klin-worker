"""
Summary API router (V3).

POST /api/summary — generate a combined summary for the given files.

Uses the existing SummaryService (vision model for images, LLM for
text/documents) and concatenates per-file summaries into a single
combined response.
"""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

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


class SummaryResponse(BaseModel):
    """POST /api/summary response body."""

    summary: str


# ── Dependency Injection ─────────────────────────────────────────────────


def _get_rag() -> RagService:
    from app.main import get_rag_service

    return get_rag_service()


def _get_summary(rag: RagService = Depends(_get_rag)) -> SummaryService:
    return SummaryService(rag)


# ── Route ────────────────────────────────────────────────────────────────


@router.post("/summary", response_model=SummaryResponse)
async def summarise_files(
    body: SummaryRequest,
    summary_svc: SummaryService = Depends(_get_summary),
) -> SummaryResponse:
    """
    Generate a combined summary from the provided files.

    Each file is summarised independently, then all per-file summaries
    are joined into a single text block.
    """
    logger.info("Summary request — %d file(s)", len(body.file_paths))

    summaries: list[str] = []
    for fp in body.file_paths:
        try:
            text = await summary_svc.summarise(fp)
            if text:
                summaries.append(text)
        except Exception as exc:
            logger.error("Summary failed for %s: %s", fp, exc)

    combined = " ".join(summaries) if summaries else "No summary could be generated."

    return SummaryResponse(summary=combined)
