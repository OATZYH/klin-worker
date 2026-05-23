"""Background ingest worker.

Request path uses a fast docling pass to extract summary context.
Background path reparses with a richer docling profile before inserting
content into the RAG knowledge graph.
"""

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from app.core.config import settings
from app.observability.tracing import (
    get_current_trace_id,
    start_as_current_observation,
    update_current_span,
)
from app.services.ai.llm_client import request_priority_var
from app.services.ai.rag_service import (
    configure_lightrag_for_file_search,
    ensure_rag_doc_status_compatible,
)
from app.services.files.docling_parser import DoclingParser

logger = logging.getLogger(__name__)

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff"}

IngestStatus = Literal["queued", "queue_full", "skipped_image", "not_ready", "deferred"]
TrackedIngestStatus = Literal[
    "queued",
    "processing",
    "indexed",
    "failed",
    "skipped_image",
    "not_ready",
    "queue_full",
    "deferred",
]


@dataclass(frozen=True, slots=True)
class IngestState:
    """Runtime indexing state for search freshness and operational visibility."""

    status: TrackedIngestStatus
    error: str | None = None
    updated_at: datetime | None = None
    elapsed_ms: float | None = None


@dataclass(frozen=True, slots=True)
class PreparedIngestResult:
    """Request-local parse result reused by the organize pipeline.

    When ``ingest_status == "deferred"``, ``deferred_job`` carries the
    ``IngestJob`` that should be handed to ``enqueue_after_organize`` once
    the foreground organize pipeline finishes.
    """

    ingest_status: IngestStatus
    extracted_text: str | None = None
    content_list: list[dict[str, Any]] | None = None
    deferred_job: "IngestJob | None" = None


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
        self._states: dict[str, IngestState] = {}

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

    async def prepare(
        self,
        filepath: str,
        trace_id: str | None = None,
        *,
        enqueue_for_rag: bool = True,
    ) -> PreparedIngestResult:
        """Prepare request-local summary text and optionally queue background ingest.

        When ``enqueue_for_rag=False`` the parse still runs (the foreground
        pipeline needs ``content_list`` and ``extracted_text``) but the RAG
        job is NOT enqueued. Instead a ready-to-enqueue ``IngestJob`` is
        attached as ``deferred_job`` and the caller is expected to invoke
        ``enqueue_after_organize`` once foreground work completes. This keeps
        the background worker out of the LLM slot while user-visible
        summary/rename/classify/schedule extraction is in flight.
        """
        path = Path(filepath)
        trace_id = trace_id or get_current_trace_id()

        if path.suffix.lower() in _IMAGE_EXTENSIONS:
            image_payload = [{"type": "image", "img_path": str(path), "page_idx": 0}]
            image_job = IngestJob(
                filepath=filepath,
                content_list=image_payload,
                trace_id=trace_id,
            )

            if not enqueue_for_rag:
                # Defer: don't touch state yet; enqueue_after_organize will.
                return PreparedIngestResult(
                    ingest_status="deferred",
                    deferred_job=image_job,
                )

            queue_status: IngestStatus = "skipped_image"
            if self._can_queue_rag_jobs():
                queue_result = self._enqueue_rag_job(image_job)
                if queue_result == "queue_full":
                    queue_status = queue_result
            else:
                queue_status = "not_ready"
                self._set_state(filepath, "not_ready", error="RAG service is not ready.")
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

        if not enqueue_for_rag:
            return PreparedIngestResult(
                ingest_status="deferred",
                extracted_text=extracted_text,
                content_list=content_list,
                deferred_job=IngestJob(filepath=filepath, trace_id=trace_id),
            )

        queue_status = "not_ready"
        if self._can_queue_rag_jobs():
            queue_status = self._enqueue_rag_job(
                IngestJob(filepath=filepath, trace_id=trace_id)
            )
        else:
            self._set_state(filepath, "not_ready", error="RAG service is not ready.")
        return PreparedIngestResult(
            ingest_status=queue_status,
            extracted_text=extracted_text,
            content_list=content_list,
        )

    async def enqueue_after_organize(
        self, prepared: PreparedIngestResult
    ) -> IngestStatus:
        """Actually enqueue a deferred ingest job after foreground work finishes.

        Returns the resulting ``IngestStatus`` (queued/queue_full/not_ready).
        Safe to call when ``prepared`` is None or has no deferred job.
        """
        if prepared is None or prepared.deferred_job is None:
            return prepared.ingest_status if prepared is not None else "not_ready"

        job = prepared.deferred_job
        if not self._can_queue_rag_jobs():
            self._set_state(job.filepath, "not_ready", error="RAG service is not ready.")
            return "not_ready"
        return self._enqueue_rag_job(job)

    async def enqueue(self, filepath: str, trace_id: str | None = None) -> PreparedIngestResult:
        """Backward-compatible wrapper for prepare(queue=True)."""
        return await self.prepare(filepath, trace_id=trace_id, enqueue_for_rag=True)

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    def is_pending(self, file_path: str) -> bool:
        """Check if a file is still waiting to be ingested."""
        return file_path in self._pending

    def pending_count(self) -> int:
        """Return number of files queued or actively indexing."""
        return len(self._pending)

    def get_state(self, file_path: str) -> IngestState | None:
        """Return the latest runtime ingest state for a file path."""
        return self._states.get(file_path)

    def _can_queue_rag_jobs(self) -> bool:
        """Return whether the worker can accept jobs that should reach RAG."""
        return self._running and self._rag is not None and self._rag.is_ready

    # ── Internals ────────────────────────────────────────────────────────

    def _set_state(
        self,
        file_path: str,
        status: TrackedIngestStatus,
        *,
        error: str | None = None,
        elapsed_ms: float | None = None,
    ) -> None:
        self._states[file_path] = IngestState(
            status=status,
            error=error,
            updated_at=datetime.now(timezone.utc),
            elapsed_ms=elapsed_ms,
        )

    def _enqueue_rag_job(self, job: IngestJob) -> IngestStatus:
        """Put one background ingest job on the queue without blocking."""
        try:
            self._queue.put_nowait(job)
            self._pending.add(job.filepath)
            self._set_state(job.filepath, "queued")
            logger.debug("Enqueued for background RAG ingest: %s", job.filepath)
            return "queued"
        except asyncio.QueueFull:
            logger.warning("Ingest queue full — dropping %s", job.filepath)
            self._set_state(job.filepath, "queue_full", error="Ingest queue is full.")
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
                    input={"file_path": file_path},
                    metadata={
                        "queue_size": self._queue.qsize(),
                        "source_trace_id": item.trace_id,
                    },
                ):
                    if not self._rag or not self._rag.is_ready:
                        self._set_state(
                            file_path,
                            "not_ready",
                            error="RAG service is not ready.",
                        )
                        logger.warning(
                            "RAG not ready — skipping background ingest for %s", file_path
                        )
                        update_current_span(output={"ingested": False, "reason": "rag_not_ready"})
                        self._pending.discard(file_path)
                        self._queue.task_done()
                        continue

                    started_at = time.perf_counter()
                    self._set_state(file_path, "processing")
                    try:
                        await self._process_job(item)
                        elapsed = (time.perf_counter() - started_at) * 1000
                        self._set_state(file_path, "indexed", elapsed_ms=round(elapsed, 2))
                        logger.info(
                            "Background RAG ingest OK: %s (%.0f ms)", file_path, elapsed
                        )
                        update_current_span(output={"ingested": True, "elapsed_ms": round(elapsed, 2)})
                    except Exception as exc:
                        elapsed = (time.perf_counter() - started_at) * 1000
                        self._set_state(
                            file_path,
                            "failed",
                            error=str(exc),
                            elapsed_ms=round(elapsed, 2),
                        )
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
        """Resolve rich content for one job and insert it into RAG.

        Marks the task's request priority as ``background`` so any LLM /
        embedding call made during the RAG insert yields to foreground
        organize calls competing for the same llama-server slot.
        """
        priority_token = request_priority_var.set("background")
        try:
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
            await self._prepare_rag_engine_for_insert(job.filepath)
            ingested = await self._rag.ingest(job.filepath)
            if ingested is False:
                raise RuntimeError("RAG ingest returned false.")
            await self._verify_rag_indexed(job.filepath)
        finally:
            request_priority_var.reset(priority_token)

    async def _ingest_to_rag(
        self, content_list: list[dict[str, Any]], file_path: str
    ) -> None:
        """Insert pre-parsed content into RAG knowledge graph."""
        rag_engine, resolved_file_path, doc_id = await self._prepare_rag_engine_for_insert(
            file_path
        )
        normalized_content = self._normalize_content_for_rag(content_list)
        if not normalized_content:
            raise RuntimeError("No supported content left for RAG ingest.")
        normalized_content = self._ensure_text_anchor_for_rag(
            normalized_content,
            resolved_file_path,
        )

        if hasattr(rag_engine, "insert_content_list"):
            await rag_engine.insert_content_list(
                content_list=normalized_content,
                file_path=resolved_file_path,
                doc_id=doc_id,
            )
        else:
            # Fallback: use the original whole-file ingestion
            logger.debug(
                "insert_content_list not available — falling back to ingest() for %s",
                file_path,
            )
            ingested = await self._rag.ingest(file_path)
            if ingested is False:
                raise RuntimeError("RAG ingest returned false.")

        await self._verify_rag_indexed(resolved_file_path)

    async def _prepare_rag_engine_for_insert(self, file_path: str) -> tuple[Any, str, str]:
        """Initialize LightRAG, remove stale doc ids, and derive a stable doc id."""
        rag_engine = self._rag._rag  # noqa: SLF001 — access RAGAnything instance
        await ensure_rag_doc_status_compatible(rag_engine)
        lightrag = await configure_lightrag_for_file_search(rag_engine)
        resolved_file_path = str(Path(file_path).resolve())
        await self._delete_existing_rag_docs(lightrag, resolved_file_path)
        return rag_engine, resolved_file_path, self._stable_doc_id(resolved_file_path)

    async def _delete_existing_rag_docs(self, lightrag: Any, file_path: str) -> None:
        """Delete old LightRAG docs for the same absolute path or basename."""
        if lightrag is None or not hasattr(lightrag, "adelete_by_doc_id"):
            return

        doc_status = getattr(lightrag, "doc_status", None)
        if doc_status is None:
            return

        candidate_paths = {file_path, str(Path(file_path).resolve()), Path(file_path).name}
        doc_ids: list[str] = []
        seen: set[str] = set()

        if hasattr(doc_status, "get_doc_by_file_path"):
            for candidate_path in candidate_paths:
                try:
                    record = await doc_status.get_doc_by_file_path(candidate_path)
                except Exception:
                    record = None
                record_id = self._record_value(record, "id") if record is not None else None
                if isinstance(record_id, str) and record_id not in seen:
                    doc_ids.append(record_id)
                    seen.add(record_id)

        storage_data = getattr(doc_status, "_data", None)
        if isinstance(storage_data, dict):
            for doc_id, record in storage_data.items():
                candidate_file_path = self._record_value(record, "file_path")
                if not candidate_file_path:
                    continue
                candidate_name = Path(str(candidate_file_path)).name
                if str(candidate_file_path) in candidate_paths or candidate_name in candidate_paths:
                    if str(doc_id) not in seen:
                        doc_ids.append(str(doc_id))
                        seen.add(str(doc_id))

        for doc_id in doc_ids:
            await lightrag.adelete_by_doc_id(doc_id)
        if doc_ids:
            logger.info("Deleted %d stale RAG doc(s) before re-ingest: %s", len(doc_ids), doc_ids)

    @staticmethod
    def _stable_doc_id(file_path: str) -> str:
        digest = hashlib.md5(str(Path(file_path).resolve()).encode("utf-8")).hexdigest()
        return f"doc-klin-{digest}"

    @staticmethod
    def _ensure_text_anchor_for_rag(
        content_list: list[dict[str, Any]],
        file_path: str,
    ) -> list[dict[str, Any]]:
        """Ensure RAGAnything creates doc_status for multimodal-only files."""
        if any(item.get("type") == "text" and str(item.get("text") or "").strip() for item in content_list):
            return content_list
        return [
            {
                "type": "text",
                "text": f"Image file: {Path(file_path).name}",
                "page_idx": 0,
            },
            *content_list,
        ]

    @classmethod
    def _normalize_content_for_rag(
        cls, content_list: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Align pre-parsed content with enabled RAGAnything processors."""
        normalized: list[dict[str, Any]] = []
        for item in content_list:
            item_type = item.get("type")
            if item_type == "text":
                normalized.append(dict(item))
            elif item_type == "table":
                if settings.rag_enable_table_processing:
                    normalized.append(dict(item))
                    continue
                table_text = cls._table_item_to_text(item)
                if table_text:
                    normalized.append({
                        "type": "text",
                        "text": table_text,
                        "page_idx": item.get("page_idx", 0),
                    })
            elif item_type == "equation":
                if settings.rag_enable_equation_processing:
                    normalized.append(dict(item))
                    continue
                equation_text = cls._equation_item_to_text(item)
                if equation_text:
                    normalized.append({
                        "type": "text",
                        "text": equation_text,
                        "page_idx": item.get("page_idx", 0),
                    })
            elif item_type == "image":
                if settings.rag_enable_image_processing:
                    normalized.append(dict(item))
            else:
                normalized.append(dict(item))
        return normalized

    @classmethod
    def _table_item_to_text(cls, item: dict[str, Any]) -> str:
        parts = [
            cls._stringify_content_value(item.get("table_caption")),
            cls._stringify_content_value(item.get("table_body")),
            cls._stringify_content_value(item.get("table_footnote")),
        ]
        return "\n\n".join(part for part in parts if part)

    @classmethod
    def _equation_item_to_text(cls, item: dict[str, Any]) -> str:
        parts = [
            cls._stringify_content_value(item.get("text")),
            cls._stringify_content_value(item.get("latex")),
        ]
        return "\n\n".join(part for part in parts if part)

    @staticmethod
    def _stringify_content_value(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            return "\n".join(
                str(part).strip()
                for part in value
                if str(part).strip()
            )
        if isinstance(value, dict):
            return "\n".join(
                f"{key}: {val}"
                for key, val in value.items()
                if str(val).strip()
            ).strip()
        return str(value).strip()

    async def _verify_rag_indexed(self, file_path: str) -> None:
        """Verify LightRAG recorded a processed document with searchable chunks."""
        rag_engine = self._rag._rag  # noqa: SLF001 — access RAGAnything instance
        lightrag = getattr(rag_engine, "lightrag", None)
        doc_status = getattr(lightrag, "doc_status", None)
        if doc_status is None:
            return

        resolved_file_path = str(Path(file_path).resolve())
        record = None
        if hasattr(doc_status, "get_doc_by_file_path"):
            for candidate_path in (file_path, resolved_file_path, Path(file_path).name):
                record = await doc_status.get_doc_by_file_path(candidate_path)
                if record is not None:
                    break

        if record is None:
            storage_data = getattr(doc_status, "_data", None)
            if isinstance(storage_data, dict):
                for candidate in storage_data.values():
                    candidate_path = self._record_value(candidate, "file_path")
                    if (
                        candidate_path == file_path
                        or candidate_path == resolved_file_path
                        or Path(str(candidate_path)).name == Path(file_path).name
                    ):
                        record = candidate
                        break

        if record is None:
            raise RuntimeError("RAG did not record document status for indexed file.")

        status = str(self._record_value(record, "status") or "").lower()
        if status.endswith(".failed") or status == "failed":
            error = self._record_value(record, "error_msg") or "RAG document status is failed."
            raise RuntimeError(str(error))
        normalized_status = status.rsplit(".", 1)[-1]
        if normalized_status in {"pending", "processing"}:
            raise RuntimeError(f"RAG document status is still {normalized_status}.")

        chunks_count = self._record_value(record, "chunks_count")
        chunks_list = self._record_value(record, "chunks_list")
        if chunks_count is not None:
            try:
                if int(chunks_count) <= 0:
                    raise RuntimeError("RAG indexed zero chunks for file.")
            except (TypeError, ValueError):
                pass
        elif isinstance(chunks_list, list) and not chunks_list:
            raise RuntimeError("RAG indexed zero chunks for file.")

    @staticmethod
    def _record_value(record: Any, key: str) -> Any:
        if isinstance(record, dict):
            return record.get(key)
        return getattr(record, key, None)
