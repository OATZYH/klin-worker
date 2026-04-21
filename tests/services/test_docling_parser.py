from __future__ import annotations

from app.core.config import settings
from app.services.files.docling_parser import (
    get_docling_parser_config,
    get_docling_profile_fingerprint,
)


def test_fast_docling_profile_uses_settings(monkeypatch) -> None:
    monkeypatch.setattr(settings, "docling_parser_max_workers", 4)
    monkeypatch.setattr(settings, "docling_fast_do_ocr", True)
    monkeypatch.setattr(settings, "docling_fast_do_table_structure", False)

    config = get_docling_parser_config("fast")

    assert config.base.max_workers == 4
    assert config.profile.name == "fast"
    assert config.profile.do_ocr is True
    assert config.profile.do_table_structure is False


def test_docling_profile_fingerprint_changes_with_settings(monkeypatch) -> None:
    monkeypatch.setattr(settings, "docling_fast_do_ocr", False)
    first = get_docling_profile_fingerprint("fast")

    monkeypatch.setattr(settings, "docling_fast_do_ocr", True)
    second = get_docling_profile_fingerprint("fast")

    assert first != second