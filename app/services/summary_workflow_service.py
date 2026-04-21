"""
Summary workflow service.

Owns the application-level summary flow for the summary endpoint:
  • AI availability check
  • cache lookup
  • summary generation
  • summary persistence
  • combined response assembly

This keeps the router focused on HTTP concerns while `SummaryService`
remains focused on generating a summary for a single file.
"""

import logging
from pathlib import Path

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import File, FileAnalysis
from app.observability.tracing import observe, update_current_span
from app.services.ai.llm_client import llm_client
from app.services.ai.summary_service import SummaryService

logger = logging.getLogger(__name__)


class SummaryWorkflowService:
    """Coordinate cache-aware summary generation for multiple files."""

    def __init__(self, summary_service: SummaryService) -> None:
        self._summary_service = summary_service

    @observe(name="summary.workflow.files", capture_input=False, capture_output=False)
    async def summarise_files(
        self,
        *,
        file_paths: list[str],
        force: bool,
        db: AsyncSession,
    ) -> str:
        """Generate or reuse summaries, then combine them for the API response."""
        per_file = await self.summarise_file_items(
            file_paths=file_paths,
            force=force,
            db=db,
        )
        summaries = [summary for _, summary in per_file]

        return "\n\n".join(summaries) if summaries else "No summary could be generated."

    @observe(name="summary.workflow.items", capture_input=False, capture_output=False)
    async def summarise_file_items(
        self,
        *,
        file_paths: list[str],
        force: bool,
        db: AsyncSession,
    ) -> list[tuple[str, str]]:
        """Generate or reuse per-file summaries with display names."""
        await llm_client.ensure_general_available()
        update_current_span(
            metadata={"file_count": len(file_paths), "force": force},
        )

        items: list[tuple[str, str]] = []
        for file_path in file_paths:
            summary = await self._get_or_generate_summary(
                db=db,
                file_path=file_path,
                force=force,
            )
            if summary:
                items.append((Path(file_path).name, summary.strip()))

        return items

    @observe(name="summary.workflow.item", capture_input=False, capture_output=False)
    async def _get_or_generate_summary(
        self,
        *,
        db: AsyncSession,
        file_path: str,
        force: bool,
    ) -> str | None:
        """Return a cached summary when possible, otherwise generate and persist it."""
        if not force:
            cached = await self._get_cached_summary(db=db, file_path=file_path)
            if cached:
                logger.info("Summary cache hit | file=%s", file_path)
                update_current_span(metadata={"cache_hit": True, "file_path": file_path})
                return cached

        summary = await self._summary_service.summarise(file_path)
        update_current_span(
            metadata={"cache_hit": False, "file_path": file_path},
            output={"has_summary": bool(summary)},
        )
        if summary:
            await self._persist_summary(db=db, file_path=file_path, summary=summary)
        return summary

    @staticmethod
    async def _get_cached_summary(
        *,
        db: AsyncSession,
        file_path: str,
    ) -> str | None:
        """Return the cached summary from `file_analysis` if it exists."""
        file_record = await SummaryWorkflowService._get_file_by_current_path(
            db=db,
            file_path=file_path,
        )
        if not file_record:
            return None

        await db.refresh(file_record, attribute_names=["analysis"])
        if file_record.analysis and file_record.analysis.summary:
            return file_record.analysis.summary
        return None

    @staticmethod
    async def _persist_summary(
        *,
        db: AsyncSession,
        file_path: str,
        summary: str,
    ) -> None:
        """Write the generated summary back to `file_analysis` for future cache hits."""
        file_record = await SummaryWorkflowService._get_file_by_current_path(
            db=db,
            file_path=file_path,
        )
        if not file_record:
            return

        await db.refresh(file_record, attribute_names=["analysis"])
        if file_record.analysis:
            file_record.analysis.summary = summary
        else:
            db.add(FileAnalysis(file_id=file_record.id, summary=summary))

    @staticmethod
    async def _get_file_by_current_path(
        *,
        db: AsyncSession,
        file_path: str,
    ) -> File | None:
        """Look up a file row by its current known path."""
        current_path_col = getattr(File, "current_path")
        result = await db.execute(select(File).where(current_path_col == file_path))
        return result.scalar_one_or_none()