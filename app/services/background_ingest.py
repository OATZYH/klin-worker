"""
Background Ingest Worker — two-phase docling + RAG ingestion.

Phase 1 (inline, blocking): parse with docling → populate text_cache
Phase 2 (background queue): insert content_list into RAG knowledge graph

Phase 1 runs inside ``enqueue()`` so the text cache is populated before
the summary step needs it.  Phase 2 is serialised via an asyncio.Queue
because the local LLM is single-threaded.

Usage:
  • startup:  `await ingest_worker.start(rag_service)`
  • enqueue:  `status = await ingest_worker.enqueue(filepath, text_cache)`
  • shutdown: `await ingest_worker.stop()`
"""

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from app.services.files.docling_parser import DoclingParser
from app.services.files.text_cache import TextCache

logger = logging.getLogger(__name__)

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff"}


class BackgroundIngestWorker:
    """
    Two-phase ingest: docling parse (inline) → RAG insert (background queue).
    """

    def __init__(
        self,
        parser: DoclingParser,
        max_queue_size: int = 1000,
    ) -> None:
        self._parser = parser
        self._queue: asyncio.Queue[tuple[list[dict[str, Any]], str] | None] = (
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

    async def enqueue(self, filepath: str, text_cache: TextCache) -> str:
        """Parse file with docling (Phase 1) and queue RAG ingestion (Phase 2).

        Returns:
            ``"queued"`` — parse + enqueue succeeded
            ``"queue_full"`` — parse OK but RAG queue is full
            ``"parse_failed"`` — docling returned empty content
            ``"skipped_image"`` — image file, no docling parse needed
        """
        path = Path(filepath)

        # Images bypass docling — vision model handles summary in Step 6
        if path.suffix.lower() in _IMAGE_EXTENSIONS:
            self._enqueue_rag_item(
                [{"type": "image", "img_path": str(path), "page_idx": 0}],
                filepath,
            )
            return "skipped_image"

        # ── Phase 1: docling parse (blocking) ────────────────────────────
        content_list = await self._parser.parse(path)

        if not content_list:
            logger.warning("Docling parse returned empty for %s", path.name)
            return "parse_failed"

        extracted_text = self._parser.extract_text(content_list)
        if extracted_text:
            text_cache.set(filepath, extracted_text)

        # ── Phase 2: enqueue for background RAG ingestion ────────────────
        return self._enqueue_rag_item(content_list, filepath)

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    def is_pending(self, file_path: str) -> bool:
        """Check if a file is still waiting to be ingested."""
        return file_path in self._pending

    # ── Internals ────────────────────────────────────────────────────────

    def _enqueue_rag_item(
        self, content_list: list[dict[str, Any]], filepath: str
    ) -> str:
        """Put a parsed content list on the RAG queue (non-blocking)."""
        try:
            self._queue.put_nowait((content_list, filepath))
            self._pending.add(filepath)
            logger.debug("Enqueued for background RAG ingest: %s", filepath)
            return "queued"
        except asyncio.QueueFull:
            logger.warning("Ingest queue full — dropping %s", filepath)
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

                content_list, file_path = item

                if not self._rag or not self._rag.is_ready:
                    logger.warning(
                        "RAG not ready — skipping background ingest for %s", file_path
                    )
                    self._pending.discard(file_path)
                    self._queue.task_done()
                    continue

                started_at = time.perf_counter()
                try:
                    await self._ingest_to_rag(content_list, file_path)
                    elapsed = (time.perf_counter() - started_at) * 1000
                    logger.info(
                        "Background RAG ingest OK: %s (%.0f ms)", file_path, elapsed
                    )
                except Exception as exc:
                    elapsed = (time.perf_counter() - started_at) * 1000
                    logger.error(
                        "Background RAG ingest error: %s (%.0f ms): %s",
                        file_path,
                        elapsed,
                        exc,
                    )
                finally:
                    self._pending.discard(file_path)
                    self._queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Unexpected error in ingest worker: %s", exc)

        logger.info("Ingest worker loop exited.")

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
