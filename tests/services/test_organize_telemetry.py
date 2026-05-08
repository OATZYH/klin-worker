from __future__ import annotations

from app.services.organize.organize_telemetry import OrganizeTelemetry


def test_history_metadata_keeps_audit_fields_and_compact_ops_snapshot() -> None:
    telemetry = OrganizeTelemetry(rag_ready=True)
    telemetry.ai_status = "ready"
    telemetry.rag_status = "queued"
    telemetry.cache_reason = "full_run"
    telemetry.is_new_file = True
    telemetry.file_changed = True
    telemetry.cached = False
    telemetry.record_timing("scan", 70.43)
    telemetry.record_timing("summary", 1477.65)
    telemetry.record_total(9472.26)

    metadata = telemetry.build_history_metadata(
        suggested_names=["invoice_march_2026.pdf"],
        scores=[
            {
                "category_id": "finance",
                "name": "Finance",
                "score": 0.93,
            }
        ],
    )

    assert metadata == {
        "suggested_names": ["invoice_march_2026.pdf"],
        "all_scores": [
            {
                "category_id": "finance",
                "name": "Finance",
                "score": 0.93,
            }
        ],
        "pipeline": {
            "cache_reason": "full_run",
            "ai_status": "ready",
            "rag_status": "queued",
        },
        "timings": {"total_ms": 9472.26},
    }


def test_log_context_keeps_full_debug_snapshot() -> None:
    telemetry = OrganizeTelemetry(rag_ready=True)
    telemetry.ai_status = "ready"
    telemetry.rag_status = "queued"
    telemetry.cache_reason = "full_run"
    telemetry.is_new_file = True
    telemetry.file_changed = True
    telemetry.cached = False
    telemetry.record_timing("scan", 70.43)

    context = telemetry.build_log_context(
        filepath="/tmp/invoice.pdf",
        file_id="file-123",
        error="summary failed",
    )

    assert context == {
        "filepath": "/tmp/invoice.pdf",
        "file_id": "file-123",
        "error": "summary failed",
        "pipeline": {
            "rag_ready": True,
            "ai_status": "ready",
            "is_new_file": True,
            "file_changed": True,
            "rag_status": "queued",
            "cached": False,
            "cache_reason": "full_run",
        },
        "timings": {"scan_ms": 70.43},
    }