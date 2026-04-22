"""Summary API router for single-file summaries.

POST /api/summary returns the cached or freshly generated summary for one
file. POST /api/summary/stream exposes the same result over SSE.
"""

import logging
import json
from time import perf_counter

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.ai_exceptions import (
    AiCapabilityUnavailableError,
    to_service_unavailable_http_exception,
)
from app.db.session import get_db
from app.observability.tracing import observe, update_current_span
from app.services.background_ingest import BackgroundIngestWorker
from app.services.ai.summary_service import SummaryService
from app.services.summary_workflow_service import SummaryWorkflowService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["summary"])


# ── Request / Response models ────────────────────────────────────────────


class SummaryRequest(BaseModel):
    """POST /api/summary request body."""

    file_path: str = Field(
        ...,
        min_length=1,
        description="Absolute file path to summarise.",
    )


class SummaryResponse(BaseModel):
    """POST /api/summary response body."""

    summary: str
    processing_time_ms: int = 0


# ── Dependency Injection ─────────────────────────────────────────────────


def _get_summary() -> SummaryService:
    return SummaryService()


def _get_ingest_worker() -> BackgroundIngestWorker:
    from app.main import ingest_worker

    return ingest_worker


def _get_summary_workflow(
    summary_svc: SummaryService = Depends(_get_summary),
    ingest_worker: BackgroundIngestWorker = Depends(_get_ingest_worker),
) -> SummaryWorkflowService:
    return SummaryWorkflowService(summary_svc, ingest_worker)


def _empty_summary_text() -> str:
    return (
        "No summary could be generated for the selected file. "
        "Ensure the file is readable and contains extractable text, then try again."
    )


# ── Route ────────────────────────────────────────────────────────────────


@router.post("/summary", response_model=SummaryResponse)
@observe(name="summary.request", capture_input=False, capture_output=False)
async def summarise_file(
    body: SummaryRequest,
    db: AsyncSession = Depends(get_db),
    workflow: SummaryWorkflowService = Depends(_get_summary_workflow),
) -> SummaryResponse:
    """
    Generate a summary for the provided file.

    Always generate a fresh summary for the file, then persist it back to
    file_analysis.
    """
    logger.info("Summary request | file=%s", body.file_path)
    update_current_span(
        input={"file_path": body.file_path},
        metadata={"feature": "summary"},
    )

    started = perf_counter()

    try:
        summary = await workflow.summarise_file(
            file_path=body.file_path,
            db=db,
        )
    except AiCapabilityUnavailableError as exc:
        raise to_service_unavailable_http_exception(exc) from exc
    except Exception as exc:
        logger.error("Summary request failed: %s", exc)
        summary = None

    resolved_summary = summary or _empty_summary_text()
    elapsed_ms = int((perf_counter() - started) * 1000)

    return SummaryResponse(
        summary=resolved_summary,
        processing_time_ms=elapsed_ms,
    )


@router.post("/summary/stream")
@observe(name="summary.stream_request", capture_input=False, capture_output=False)
async def summarise_file_stream(
    body: SummaryRequest,
    db: AsyncSession = Depends(get_db),
    workflow: SummaryWorkflowService = Depends(_get_summary_workflow),
) -> StreamingResponse:
    """Stream a single-file summary over SSE."""
    started = perf_counter()
    logger.info("Summary stream request | file=%s", body.file_path)
    update_current_span(
        input={"file_path": body.file_path},
        metadata={"feature": "summary_stream"},
    )

    try:
        summary = await workflow.summarise_file(
            file_path=body.file_path,
            db=db,
        )
    except AiCapabilityUnavailableError as exc:
        raise to_service_unavailable_http_exception(exc) from exc
    except Exception as exc:
        logger.error("Summary stream request failed: %s", exc)
        summary = None

    async def event_generator():
        resolved_summary = summary or _empty_summary_text()
        yield f"event: chunk\ndata: {json.dumps({'delta': resolved_summary}, ensure_ascii=False)}\n\n"

        elapsed_ms = int((perf_counter() - started) * 1000)
        done_payload = json.dumps(
            {
                "processing_time_ms": elapsed_ms,
            },
            ensure_ascii=False,
        )
        yield f"event: done\ndata: {done_payload}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
