"""Background ingest worker.

Request path uses a fast docling pass to extract summary context.
Background path reparses with a richer docling profile before inserting
content into the RAG knowledge graph.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from app.observability.tracing import get_current_trace_id, start_as_current_observation, update_current_span
from app.services.files.docling_parser import DoclingParser

logger = logging.getLogger(__name__)

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff"}

IngestStatus = Literal["queued", "queue_full", "skipped_image"]


@dataclass(frozen=True, slots=True)
class PreparedIngestResult:
    """Request-local parse result reused by the organize pipeline."""

    ingest_status: IngestStatus
    extracted_text: str | None = None


@dataclass(frozen=True, slots=True)
class IngestJob:
    """Background ingest job queued for the worker loop."""

    filepath: str
    content_list: list[dict[str, Any]] | None = None
    trace_id: str | None = None


class BackgroundIngestWorker:
    """
    Two-phase ingest: docling parse (inline) → RAG insert (background queue).
    """

    def __init__(
        self,
        fast_parser: DoclingParser,
        rich_parser: DoclingParser,
        max_queue_size: int = 1000,
    ) -> None:
        self._fast_parser = fast_parser
        self._rich_parser = rich_parser
        self._queue: asyncio.Queue[IngestJob | None] = (
            asyncio.Queue(maxsize=max_queue_size)
        )
        self._rag: Any = None
        self._task: asyncio.Task[None] | None = None
        self._running: bool = False
        self._pending: set[str] = set()

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def start(self, rag_service: Any) -> None:
        """Start the background worker loop."""
        if self._running:
            return
        self._rag = rag_service
        self._running = True
        self._task = asyncio.create_task(self._worker_loop(), name="rag-ingest-worker")
        logger.info("Background ingest worker started.")

    async def stop(self) -> None:
        """Gracefully stop the worker, draining the queue first."""
        if not self._running:
            return
        self._running = False
        await self._queue.put(None)
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=30.0)
            except asyncio.TimeoutError:
                logger.warning("Background ingest worker did not stop in time — cancelling.")
                self._task.cancel()
            self._task = None
        logger.info("Background ingest worker stopped.")

    # ── Public API ───────────────────────────────────────────────────────

    async def enqueue(self, filepath: str, trace_id: str | None = None) -> PreparedIngestResult:
        """Prepare request-local summary text and queue background ingest."""
        path = Path(filepath)
        trace_id = trace_id or get_current_trace_id()

        if path.suffix.lower() in _IMAGE_EXTENSIONS:
            queue_status = self._enqueue_rag_job(
                IngestJob(
                    filepath=filepath,
                    content_list=[{"type": "image", "img_path": str(path), "page_idx": 0}],
                    trace_id=trace_id,
                )
            )
            if queue_status == "queued":
                queue_status = "skipped_image"
            return PreparedIngestResult(ingest_status=queue_status)

        content_list = await self._fast_parser.parse(path)
        extracted_text = None
        if content_list:
            extracted_text = self._fast_parser.extract_text(content_list) or None
        else:
            logger.warning(
                "Docling[%s] parse returned empty for %s",
                self._fast_parser.profile_name,
                path.name,
            )

        queue_status = self._enqueue_rag_job(IngestJob(filepath=filepath, trace_id=trace_id))
        return PreparedIngestResult(
            ingest_status=queue_status,
            extracted_text=extracted_text,
        )

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    def is_pending(self, file_path: str) -> bool:
        """Check if a file is still waiting to be ingested."""
        return file_path in self._pending

    # ── Internals ────────────────────────────────────────────────────────

    def _enqueue_rag_job(self, job: IngestJob) -> IngestStatus:
        """Put one background ingest job on the queue without blocking."""
        try:
            self._queue.put_nowait(job)
            self._pending.add(job.filepath)
            logger.debug("Enqueued for background RAG ingest: %s", job.filepath)
            return "queued"
        except asyncio.QueueFull:
            logger.warning("Ingest queue full — dropping %s", job.filepath)
            return "queue_full"

    async def _worker_loop(self) -> None:
        """Process items from the queue until stopped."""
        logger.info("Ingest worker loop running.")
        while self._running:
            try:
                item = await self._queue.get()

                if item is None:
                    self._queue.task_done()
                    break

                file_path = item.filepath

                with start_as_current_observation(
                    name="background_ingest.process_job",
                    as_type="span",
                    trace_context={"trace_id": item.trace_id} if item.trace_id else None,
                    input={"file_path": file_path},
                    metadata={"queue_size": self._queue.qsize()},
                ):
                    if not self._rag or not self._rag.is_ready:
                        logger.warning(
                            "RAG not ready — skipping background ingest for %s", file_path
                        )
                        update_current_span(output={"ingested": False, "reason": "rag_not_ready"})
                        self._pending.discard(file_path)
                        self._queue.task_done()
                        continue

                    started_at = time.perf_counter()
                    try:
                        await self._process_job(item)
                        elapsed = (time.perf_counter() - started_at) * 1000
                        logger.info(
                            "Background RAG ingest OK: %s (%.0f ms)", file_path, elapsed
                        )
                        update_current_span(output={"ingested": True, "elapsed_ms": round(elapsed, 2)})
                    except Exception as exc:
                        elapsed = (time.perf_counter() - started_at) * 1000
                        logger.error(
                            "Background RAG ingest error: %s (%.0f ms): %s",
                            file_path,
                            elapsed,
                            exc,
                        )
                        update_current_span(
                            output={"ingested": False, "elapsed_ms": round(elapsed, 2)},
                            level="ERROR",
                            status_message=str(exc),
                        )
                    finally:
                        self._pending.discard(file_path)
                        self._queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Unexpected error in ingest worker: %s", exc)

        logger.info("Ingest worker loop exited.")

    async def _process_job(self, job: IngestJob) -> None:
        """Resolve rich content for one job and insert it into RAG."""
        if job.content_list is not None:
            await self._ingest_to_rag(job.content_list, job.filepath)
            return

        content_list = await self._rich_parser.parse(job.filepath)
        if content_list:
            await self._ingest_to_rag(content_list, job.filepath)
            return

        logger.warning(
            "Docling[%s] parse returned empty for %s — falling back to rag.ingest()",
            self._rich_parser.profile_name,
            job.filepath,
        )
        await self._rag.ingest(job.filepath)

    async def _ingest_to_rag(
        self, content_list: list[dict[str, Any]], file_path: str
    ) -> None:
        """Insert pre-parsed content into RAG knowledge graph."""
        rag_engine = self._rag._rag  # noqa: SLF001 — access RAGAnything instance

        if hasattr(rag_engine, "insert_content_list"):
            await rag_engine.insert_content_list(
                content_list=content_list,
                file_path=file_path,
            )
        else:
            # Fallback: use the original whole-file ingestion
            logger.debug(
                "insert_content_list not available — falling back to ingest() for %s",
                file_path,
            )
            await self._rag.ingest(file_path)
