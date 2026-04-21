from __future__ import annotations

import pytest

from app.services.lock_settings_service import LockSettingsService, LockSettingsSnapshot


def _empty_snapshot() -> LockSettingsSnapshot:
    return LockSettingsSnapshot(
        lock_file=[],
        lock_folder=[],
        lock_file_updated_at=None,
        lock_folder_updated_at=None,
    )


def test_normalize_paths_dedupes_case_and_slash_variants() -> None:
    paths = [
        "  C:\\Work\\Doc.pdf  ",
        "c:/work/doc.pdf",
        "C:/WORK/DOC.PDF/",
        "",
    ]

    normalized = LockSettingsService.normalize_paths(paths)

    assert normalized == ["C:\\Work\\Doc.pdf"]


def test_decode_paths_handles_invalid_json_and_non_list() -> None:
    assert LockSettingsService.decode_paths("not-json") == []
    assert LockSettingsService.decode_paths('{"a": 1}') == []


def test_get_lock_reason_matches_exact_file() -> None:
    snapshot = LockSettingsSnapshot(
        lock_file=["C:\\Docs\\A.pdf"],
        lock_folder=[],
        lock_file_updated_at=None,
        lock_folder_updated_at=None,
    )

    reason = LockSettingsService.get_lock_reason("c:/docs/a.pdf", snapshot)

    assert reason == "file is directly locked: C:\\Docs\\A.pdf"


def test_get_lock_reason_matches_folder_descendant() -> None:
    snapshot = LockSettingsSnapshot(
        lock_file=[],
        lock_folder=["C:\\Users\\me\\Secret"],
        lock_file_updated_at=None,
        lock_folder_updated_at=None,
    )

    reason = LockSettingsService.get_lock_reason(
        "C:/Users/me/Secret/sub/report.docx",
        snapshot,
    )

    assert reason == "file is locked by folder: C:\\Users\\me\\Secret"


def test_partition_paths_splits_locked_and_unlocked() -> None:
    snapshot = LockSettingsSnapshot(
        lock_file=["/private/a.pdf"],
        lock_folder=["/private/secret"],
        lock_file_updated_at=None,
        lock_folder_updated_at=None,
    )

    unlocked, locked = LockSettingsService.partition_paths(
        file_paths=["/private/a.pdf", "/private/public/b.pdf", "/private/secret/x.txt"],
        settings=snapshot,
    )

    assert unlocked == ["/private/public/b.pdf"]
    assert [(item.path, item.reason) for item in locked] == [
        ("/private/a.pdf", "file is directly locked: /private/a.pdf"),
        ("/private/secret/x.txt", "file is locked by folder: /private/secret"),
    ]


def test_validate_absolute_paths_accepts_unix_and_windows() -> None:
    LockSettingsService.validate_absolute_paths(
        paths=["/Users/me/file.pdf", "C:\\Users\\me\\file.pdf"],
        field_name="lock_file",
    )


def test_validate_absolute_paths_rejects_relative_paths() -> None:
    with pytest.raises(ValueError, match="lock_folder"):
        LockSettingsService.validate_absolute_paths(
            paths=["relative/file.pdf", "./tmp"],
            field_name="lock_folder",
        )


def test_get_lock_reason_returns_none_for_unlocked_path() -> None:
    reason = LockSettingsService.get_lock_reason("/tmp/a.txt", _empty_snapshot())
    assert reason is None
