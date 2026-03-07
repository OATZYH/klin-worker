"""
Background Ingest Worker — async queue-based RAG ingestion.

Decouples the heavy RAG-Anything document ingestion from the
synchronous organize pipeline so API responses return fast.

Usage:
  • startup:  `await ingest_worker.start(rag_service)`
  • enqueue:  `ingest_worker.enqueue(file_path)`
  • shutdown: `await ingest_worker.stop()`
"""

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class BackgroundIngestWorker:
    """
    Consumes file paths from an asyncio.Queue and ingests them into RAG
    in the background, one at a time (local model is single-threaded).
    """

    def __init__(self, max_queue_size: int = 1000) -> None:
        self._queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=max_queue_size)
        self._rag: Any = None
        self._task: asyncio.Task | None = None
        self._running: bool = False
        # Track in-flight paths so callers can check status
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
        # Send sentinel to unblock the worker
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

    def enqueue(self, file_path: str) -> bool:
        """
        Add a file path to the ingestion queue.

        Returns True if enqueued, False if queue is full.
        """
        try:
            self._queue.put_nowait(file_path)
            self._pending.add(file_path)
            logger.debug("Enqueued for background ingest: %s", file_path)
            return True
        except asyncio.QueueFull:
            logger.warning("Ingest queue full — dropping %s", file_path)
            return False

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    def is_pending(self, file_path: str) -> bool:
        """Check if a file is still waiting to be ingested."""
        return file_path in self._pending

    # ── Worker loop ──────────────────────────────────────────────────────

    async def _worker_loop(self) -> None:
        """Process items from the queue until stopped."""
        logger.info("Ingest worker loop running.")
        while self._running:
            try:
                file_path = await self._queue.get()

                # Sentinel value signals shutdown
                if file_path is None:
                    self._queue.task_done()
                    break

                if not self._rag or not self._rag.is_ready:
                    logger.warning(
                        "RAG not ready — skipping background ingest for %s", file_path
                    )
                    self._pending.discard(file_path)
                    self._queue.task_done()
                    continue

                started_at = time.perf_counter()
                try:
                    ok = await self._rag.ingest(file_path)
                    elapsed = (time.perf_counter() - started_at) * 1000
                    if ok:
                        logger.info(
                            "Background ingest OK: %s (%.0f ms)", file_path, elapsed
                        )
                    else:
                        logger.warning(
                            "Background ingest failed: %s (%.0f ms)", file_path, elapsed
                        )
                except Exception as exc:
                    elapsed = (time.perf_counter() - started_at) * 1000
                    logger.error(
                        "Background ingest error: %s (%.0f ms): %s",
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


# ── Module-level singleton ───────────────────────────────────────────────

ingest_worker = BackgroundIngestWorker()
