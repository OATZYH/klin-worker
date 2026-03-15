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
import json
from pathlib import Path
from time import perf_counter

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.ai_exceptions import (
    AiCapabilityUnavailableError,
    to_service_unavailable_http_exception,
)
from app.core.config import settings
from app.db.session import get_db
from app.services.ai.llm_client import llm_client
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
    suggested_title: str = "Quick-Note"
    processing_time_ms: int = 0


# ── Dependency Injection ─────────────────────────────────────────────────


def _get_summary() -> SummaryService:
    return SummaryService()


def _get_summary_workflow(
    summary_svc: SummaryService = Depends(_get_summary),
) -> SummaryWorkflowService:
    return SummaryWorkflowService(summary_svc)


def _build_suggested_title(file_paths: list[str]) -> str:
    """Generate a stable title suggestion from the first file name."""
    if len(file_paths) > 1:
        return f"Summary - {len(file_paths)} files"

    first = file_paths[0] if file_paths else ""
    stem = Path(first).stem.strip() if first else ""
    cleaned = stem.replace("_", " ").replace("+", " ").strip()
    return f"Summary - {cleaned}"[:120] if cleaned else "Quick-Note"


def _fallback_markdown(per_file_summaries: list[tuple[str, str]]) -> str:
    """Structured markdown fallback when synthesis is unavailable."""
    lines: list[str] = [
        "## Overview",
        "This summary combines insights from the selected files.",
        "",
        "## Key Points",
    ]

    for file_name, text in per_file_summaries:
        short = " ".join(text.split())
        if len(short) > 220:
            short = short[:220].rstrip() + "..."
        lines.append(f"- **{file_name}**: {short}")

    lines.extend(["", "## Per-File Insights"])

    for file_name, text in per_file_summaries:
        lines.extend(
            [
                f"### {file_name}",
                text.strip() or "Not enough extracted content to produce a detailed summary.",
                "",
            ]
        )

    lines.extend(
        [
            "## Suggested Actions",
            "1. Review the per-file section and verify critical details against the source documents.",
            "2. Add missing facts (names, dates, metrics) where extracted content is limited.",
        ]
    )

    return "\n".join(lines).strip()


async def _compose_markdown_summary(per_file_summaries: list[tuple[str, str]]) -> str:
    """Synthesize richer markdown from per-file summaries using the local LLM."""
    context = "\n\n".join(
        f"File: {file_name}\nSummary: {summary_text}"
        for file_name, summary_text in per_file_summaries
    )

    prompt = (
        "You are an expert document summarization assistant. "
        "Using the provided file summaries, produce a clear, detailed markdown summary for end users.\n\n"
        "Output requirements:\n"
        "- Return markdown only.\n"
        "- Be informative and readable (not too short).\n"
        "- Use this exact section structure (H2 headings):\n"
        "  ## Overview\n"
        "  ## Key Points\n"
        "  ## Per-File Insights\n"
        "  ## Suggested Actions\n"
        "- In 'Overview', write 1 to 2 paragraphs.\n"
        "- In 'Key Points', provide at least 5 bullets that synthesize across files.\n"
        "- In 'Per-File Insights', include one H3 subsection per file and summarize key content, important details, and missing context.\n"
        "- In 'Suggested Actions', provide actionable next steps as a numbered list.\n"
        "- If content is missing or uncertain, explicitly say what is unclear instead of inventing facts.\n\n"
        "File summaries:\n"
        f"{context}"
    )

    markdown = await llm_client.achat(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.25,
        max_tokens=max(settings.summary_max_tokens * 3, 700),
    )

    return markdown.strip()


def _build_compose_prompt(per_file_summaries: list[tuple[str, str]]) -> str:
    """Build the markdown synthesis prompt from per-file summaries."""
    context = "\n\n".join(
        f"File: {file_name}\nSummary: {summary_text}"
        for file_name, summary_text in per_file_summaries
    )

    return (
        "You are an expert document summarization assistant. "
        "Using the provided file summaries, produce a clear, detailed markdown summary for end users.\n\n"
        "Output requirements:\n"
        "- Return markdown only.\n"
        "- Be informative and readable (not too short).\n"
        "- Use this exact section structure (H2 headings):\n"
        "  ## Overview\n"
        "  ## Key Points\n"
        "  ## Per-File Insights\n"
        "  ## Suggested Actions\n"
        "- In 'Overview', write 1 to 2 paragraphs.\n"
        "- In 'Key Points', provide at least 5 bullets that synthesize across files.\n"
        "- In 'Per-File Insights', include one H3 subsection per file and summarize key content, important details, and missing context.\n"
        "- In 'Suggested Actions', provide actionable next steps as a numbered list.\n"
        "- If content is missing or uncertain, explicitly say what is unclear instead of inventing facts.\n\n"
        "File summaries:\n"
        f"{context}"
    )


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

    started = perf_counter()

    try:
        per_file_summaries = await workflow.summarise_file_items(
            file_paths=body.file_paths,
            force=body.force,
            db=db,
        )
    except AiCapabilityUnavailableError as exc:
        raise to_service_unavailable_http_exception(exc) from exc
    except Exception as exc:
        logger.error("Summary request failed: %s", exc)
        per_file_summaries = []

    suggested_title = _build_suggested_title(body.file_paths)

    if not per_file_summaries:
        elapsed_ms = int((perf_counter() - started) * 1000)
        return SummaryResponse(
            summary=(
                "## Overview\n"
                "No summary could be generated from the selected files.\n\n"
                "## Suggested Actions\n"
                "1. Ensure files are readable and contain extractable text.\n"
                "2. Retry after the files are ingested by the semantic engine."
            ),
            suggested_title=suggested_title,
            processing_time_ms=elapsed_ms,
        )

    try:
        combined_markdown = await _compose_markdown_summary(per_file_summaries)
    except Exception as exc:
        logger.warning("Combined markdown synthesis failed, using fallback format: %s", exc)
        combined_markdown = _fallback_markdown(per_file_summaries)

    elapsed_ms = int((perf_counter() - started) * 1000)

    return SummaryResponse(
        summary=combined_markdown,
        suggested_title=suggested_title,
        processing_time_ms=elapsed_ms,
    )


@router.post("/summary/stream")
async def summarise_files_stream(
    body: SummaryRequest,
    db: AsyncSession = Depends(get_db),
    workflow: SummaryWorkflowService = Depends(_get_summary_workflow),
) -> StreamingResponse:
    """Stream markdown summary chunks over SSE for progressive UI rendering."""
    started = perf_counter()
    logger.info(
        "Summary stream request — %d file(s), force=%s",
        len(body.file_paths),
        body.force,
    )

    try:
        per_file_summaries = await workflow.summarise_file_items(
            file_paths=body.file_paths,
            force=body.force,
            db=db,
        )
    except AiCapabilityUnavailableError as exc:
        raise to_service_unavailable_http_exception(exc) from exc
    except Exception as exc:
        logger.error("Summary stream request failed: %s", exc)
        per_file_summaries = []

    suggested_title = _build_suggested_title(body.file_paths)

    async def event_generator():
        meta_payload = json.dumps({"suggested_title": suggested_title}, ensure_ascii=False)
        yield f"event: meta\ndata: {meta_payload}\n\n"

        if not per_file_summaries:
            fallback_text = (
                "## Overview\n"
                "No summary could be generated from the selected files.\n\n"
                "## Suggested Actions\n"
                "1. Ensure files are readable and contain extractable text.\n"
                "2. Retry after the files are ingested by the semantic engine."
            )
            yield f"event: chunk\ndata: {json.dumps({'delta': fallback_text}, ensure_ascii=False)}\n\n"
        else:
            prompt = _build_compose_prompt(per_file_summaries)
            try:
                async for token in llm_client.achat_stream(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.25,
                    max_tokens=max(settings.summary_max_tokens * 3, 700),
                ):
                    yield f"event: chunk\ndata: {json.dumps({'delta': token}, ensure_ascii=False)}\n\n"
            except Exception as exc:
                logger.warning("Summary stream failed, using fallback format: %s", exc)
                fallback_text = _fallback_markdown(per_file_summaries)
                yield f"event: chunk\ndata: {json.dumps({'delta': fallback_text}, ensure_ascii=False)}\n\n"

        elapsed_ms = int((perf_counter() - started) * 1000)
        done_payload = json.dumps(
            {
                "processing_time_ms": elapsed_ms,
                "suggested_title": suggested_title,
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
