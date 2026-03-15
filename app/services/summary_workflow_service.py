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
from app.services.ai.llm_client import llm_client
from app.services.observability import safe_start_observation, safe_update_observation
from app.services.ai.summary_service import SummaryService

logger = logging.getLogger(__name__)


class SummaryWorkflowService:
    """Coordinate cache-aware summary generation for multiple files."""

    def __init__(self, summary_service: SummaryService) -> None:
        self._summary_service = summary_service

    async def summarise_files(
        self,
        *,
        file_paths: list[str],
        force: bool,
        db: AsyncSession,
    ) -> str:
        """Generate or reuse summaries, then combine them for the API response."""
        with safe_start_observation(
            name="summary.workflow.combine",
            input_payload={"file_count": len(file_paths), "force": force},
        ) as span:
            per_file = await self.summarise_file_items(
                file_paths=file_paths,
                force=force,
                db=db,
            )
            summaries = [summary for _, summary in per_file]
            combined = "\n\n".join(summaries) if summaries else "No summary could be generated."

            safe_update_observation(
                span,
                output={"generated_file_count": len(per_file), "combined_length": len(combined)},
            )
            return combined

    async def summarise_file_items(
        self,
        *,
        file_paths: list[str],
        force: bool,
        db: AsyncSession,
    ) -> list[tuple[str, str]]:
        """Generate or reuse per-file summaries with display names."""
        with safe_start_observation(
            name="summary.workflow.files",
            input_payload={"file_paths": file_paths, "force": force},
        ) as span:
            await llm_client.ensure_general_available()

            items: list[tuple[str, str]] = []
            for file_path in file_paths:
                with safe_start_observation(
                    name="summary.workflow.file",
                    metadata={"file_path": file_path, "force": force},
                ) as file_span:
                    summary = await self._get_or_generate_summary(
                        db=db,
                        file_path=file_path,
                        force=force,
                    )
                    if summary:
                        cleaned = summary.strip()
                        items.append((Path(file_path).name, cleaned))
                        safe_update_observation(
                            file_span,
                            output={"summary_length": len(cleaned)},
                        )

            safe_update_observation(
                span,
                output={"summaries_created": len(items), "file_count": len(file_paths)},
            )
            return items

    async def _get_or_generate_summary(
        self,
        *,
        db: AsyncSession,
        file_path: str,
        force: bool,
    ) -> str | None:
        """Return a cached summary when possible, otherwise generate and persist it."""
        with safe_start_observation(
            name="summary.workflow.get_or_generate",
            metadata={"file_path": file_path, "force": force},
        ) as span:
            if not force:
                cached = await self._get_cached_summary(db=db, file_path=file_path)
                if cached:
                    logger.info("Summary cache hit | file=%s", file_path)
                    safe_update_observation(
                        span,
                        output={"cache": "hit", "summary_length": len(cached)},
                    )
                    return cached

            summary = await self._summary_service.summarise(file_path)
            if summary:
                await self._persist_summary(db=db, file_path=file_path, summary=summary)
                safe_update_observation(
                    span,
                    output={"cache": "miss", "summary_length": len(summary)},
                )
            else:
                safe_update_observation(
                    span,
                    output={"cache": "miss", "summary_length": 0},
                )
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