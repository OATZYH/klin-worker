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

from app.core.ai_exceptions import (
    AiCapabilityUnavailableError,
    to_service_unavailable_http_exception,
)
from app.services.rag_service import RagService
from app.services.summary_service import SummaryService
from app.services.llm_client import llm_client

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

    try:
        await llm_client.ensure_general_available()
    except AiCapabilityUnavailableError as exc:
        raise to_service_unavailable_http_exception(exc) from exc

    summaries: list[str] = []
    for fp in body.file_paths:
        try:
            text = await summary_svc.summarise(fp)
            if text:
                summaries.append(text)
        except AiCapabilityUnavailableError as exc:
            raise to_service_unavailable_http_exception(exc) from exc
        except Exception as exc:
            logger.error("Summary failed for %s: %s", fp, exc)

    combined = " ".join(summaries) if summaries else "No summary could be generated."

    return SummaryResponse(summary=combined)
