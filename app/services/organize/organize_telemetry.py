from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


PipelineValue = str | bool | None


@dataclass(slots=True)
class OrganizeTelemetry:
    """Own organize pipeline state and serialize it per destination."""

    rag_ready: bool
    ai_status: str | None = None
    is_new_file: bool = False
    file_changed: bool = False
    rag_status: str | None = None
    cached: bool | str = False
    cache_reason: str | None = None
    timings: dict[str, float] = field(default_factory=dict)

    def record_timing(self, name: str, elapsed_ms: float) -> None:
        self.timings[f"{name}_ms"] = elapsed_ms

    def record_total(self, elapsed_ms: float) -> None:
        self.timings["total_ms"] = elapsed_ms

    def pipeline_dict(self) -> dict[str, PipelineValue]:
        return {
            "rag_ready": self.rag_ready,
            "ai_status": self.ai_status,
            "is_new_file": self.is_new_file,
            "file_changed": self.file_changed,
            "rag_status": self.rag_status,
            "cached": self.cached,
            "cache_reason": self.cache_reason,
        }

    def timings_dict(self) -> dict[str, float]:
        return dict(self.timings)

    def history_pipeline_dict(self) -> dict[str, str]:
        pipeline: dict[str, str] = {}
        if self.cache_reason is not None:
            pipeline["cache_reason"] = self.cache_reason
        if self.ai_status is not None:
            pipeline["ai_status"] = self.ai_status
        if self.rag_status is not None:
            pipeline["rag_status"] = self.rag_status
        return pipeline

    def history_timings_dict(self) -> dict[str, float]:
        total_ms = self.timings.get("total_ms")
        if total_ms is None:
            return {}
        return {"total_ms": total_ms}

    def build_history_metadata(
        self,
        *,
        suggested_names: list[str],
        scores: list[dict[str, Any]],
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "suggested_names": suggested_names,
            "all_scores": [
                {
                    "category_id": str(score["category_id"]),
                    "name": str(score["name"]),
                    "score": float(score["score"]),
                }
                for score in scores
            ],
        }

        pipeline = self.history_pipeline_dict()
        if pipeline:
            metadata["pipeline"] = pipeline

        timings = self.history_timings_dict()
        if timings:
            metadata["timings"] = timings

        return metadata

    def build_log_context(
        self,
        *,
        filepath: str,
        file_id: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        context: dict[str, Any] = {"filepath": filepath}
        if file_id:
            context["file_id"] = file_id
        if error:
            context["error"] = error

        context["pipeline"] = self.pipeline_dict()
        if self.timings:
            context["timings"] = self.timings_dict()

        return context

    def build_trace_metadata(self, **extra: Any) -> dict[str, Any]:
        metadata: dict[str, Any] = self.pipeline_dict()
        if self.timings:
            metadata["timings"] = self.timings_dict()
        metadata.update(extra)
        return metadata