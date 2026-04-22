"""Summary workflow service.

Owns the single-file summary flow for the summary endpoints. This keeps the
router focused on HTTP concerns while `SummaryService` remains focused on
generating the summary text itself.
"""

import logging

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import File, FileAnalysis
from app.observability.tracing import get_current_trace_id, observe, update_current_span
from app.services.background_ingest import BackgroundIngestWorker
from app.services.ai.llm_client import llm_client
from app.services.ai.summary_service import SummaryService

logger = logging.getLogger(__name__)


class SummaryWorkflowService:
    """Coordinate fresh summary generation for one file."""

    def __init__(
        self,
        summary_service: SummaryService,
        ingest_worker: BackgroundIngestWorker,
    ) -> None:
        self._summary_service = summary_service
        self._ingest_worker = ingest_worker

    @observe(name="summary.workflow.file", capture_input=False, capture_output=False)
    async def summarise_file(
        self,
        *,
        file_path: str,
        db: AsyncSession,
    ) -> str | None:
        """Generate and persist the summary for one file."""
        await llm_client.ensure_general_available()
        update_current_span(
            metadata={"file_path": file_path},
        )
        summary = await self._generate_summary(
            db=db,
            file_path=file_path,
        )
        return summary.strip() if summary else None

    @observe(name="summary.workflow.item", capture_input=False, capture_output=False)
    async def _generate_summary(
        self,
        *,
        db: AsyncSession,
        file_path: str,
    ) -> str | None:
        """Generate a fresh summary and replace any stored summary."""
        prepared_ingest = await self._ingest_worker.prepare(
            file_path,
            trace_id=get_current_trace_id(),
            enqueue_for_rag=False,
        )
        summary = await self._summary_service.summarise(
            file_path,
            extracted_text=prepared_ingest.extracted_text,
        )
        update_current_span(
            metadata={
                "file_path": file_path,
                "rag_status": prepared_ingest.ingest_status,
            },
            output={"has_summary": bool(summary)},
        )
        if summary:
            await self._persist_summary(db=db, file_path=file_path, summary=summary)
        return summary

    @staticmethod
    async def _persist_summary(
        *,
        db: AsyncSession,
        file_path: str,
        summary: str,
    ) -> None:
        """Write the generated summary back to `file_analysis`, replacing any existing value."""
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