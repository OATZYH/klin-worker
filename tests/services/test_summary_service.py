from __future__ import annotations

from pathlib import Path

from app.services.ai.summary_service import SummaryService


def test_get_text_context_prefers_extracted_text() -> None:
    text = "important content " * 10

    context = SummaryService._get_text_context(Path("/tmp/report.pdf"), text)

    assert context == text


def test_get_text_context_falls_back_to_filename() -> None:
    context = SummaryService._get_text_context(Path("/tmp/report.pdf"), None)

    assert context == "Filename: report.pdf, Extension: .pdf"