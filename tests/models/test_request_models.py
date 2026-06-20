"""Pydantic validation edge cases for request models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.request import (
    ApplyOrganizeDecisionRequest,
    ApplySelectedCategoryRequest,
    BatchCategoryCreate,
    CategoryCreate,
    FileSearchRequest,
    LockSettingsUpdateRequest,
    NoteHistoryCreateRequest,
    OrganizeRequest,
    WatcherFolderCreate,
    WatcherFolderUpdate,
)

pytestmark = pytest.mark.unit


# ── OrganizeRequest ──────────────────────────────────────────────────────


def test_organize_request_rejects_empty_file_paths() -> None:
    with pytest.raises(ValidationError):
        OrganizeRequest(file_paths=[])


def test_organize_request_force_defaults_to_false() -> None:
    req = OrganizeRequest(file_paths=["/tmp/a.pdf"])
    assert req.force is False


# ── ApplyOrganizeDecisionRequest ─────────────────────────────────────────


def test_apply_request_requires_at_least_one_action() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ApplyOrganizeDecisionRequest(file_id="file-1")
    assert "At least one of selected_name or selected_category" in str(exc_info.value)


def test_apply_request_accepts_only_selected_name() -> None:
    req = ApplyOrganizeDecisionRequest(file_id="file-1", selected_name="new.pdf")
    assert req.selected_name == "new.pdf"
    assert req.selected_category is None


def test_apply_request_accepts_only_selected_category() -> None:
    req = ApplyOrganizeDecisionRequest(
        file_id="file-1",
        selected_category=ApplySelectedCategoryRequest(id="cat-1", name="Work", score=92.5),
    )
    assert req.selected_category is not None
    assert req.selected_category.score == 92.5


def test_apply_selected_category_score_out_of_range() -> None:
    with pytest.raises(ValidationError):
        ApplySelectedCategoryRequest(id="x", name="x", score=101)


# ── CategoryCreate ───────────────────────────────────────────────────────


def test_category_create_rejects_blank_name() -> None:
    with pytest.raises(ValidationError):
        CategoryCreate(name="")


def test_category_create_rejects_invalid_color() -> None:
    with pytest.raises(ValidationError):
        CategoryCreate(name="Work", color="red")


def test_category_create_accepts_hex_color() -> None:
    cat = CategoryCreate(name="Work", color="#aabbcc")
    assert cat.color == "#aabbcc"


def test_category_create_rejects_name_too_long() -> None:
    with pytest.raises(ValidationError):
        CategoryCreate(name="x" * 101)


def test_batch_category_create_requires_at_least_one() -> None:
    with pytest.raises(ValidationError):
        BatchCategoryCreate(categories=[])


# ── WatcherFolderCreate ──────────────────────────────────────────────────


def test_watcher_folder_create_defaults() -> None:
    folder = WatcherFolderCreate(folder_path="/tmp/watched")
    assert folder.frequency_value == 1
    assert folder.frequency_unit == "day"
    assert folder.recursive is True
    assert folder.auto_organize_enabled is True


def test_watcher_folder_create_rejects_bad_unit() -> None:
    with pytest.raises(ValidationError):
        WatcherFolderCreate(folder_path="/tmp", frequency_unit="week")  # type: ignore[arg-type]


def test_watcher_folder_create_rejects_frequency_out_of_range() -> None:
    with pytest.raises(ValidationError):
        WatcherFolderCreate(folder_path="/tmp", frequency_value=0)
    with pytest.raises(ValidationError):
        WatcherFolderCreate(folder_path="/tmp", frequency_value=1000)


# ── WatcherFolderUpdate ──────────────────────────────────────────────────


def test_watcher_folder_update_requires_at_least_one_field() -> None:
    with pytest.raises(ValidationError) as exc_info:
        WatcherFolderUpdate()
    assert "At least one field must be provided" in str(exc_info.value)


def test_watcher_folder_update_accepts_single_field() -> None:
    update = WatcherFolderUpdate(recursive=False)
    assert update.recursive is False
    assert update.folder_path is None


# ── LockSettingsUpdateRequest ────────────────────────────────────────────


def test_lock_settings_update_defaults_to_empty_lists() -> None:
    req = LockSettingsUpdateRequest()
    assert req.lock_file == []
    assert req.lock_folder == []


# ── FileSearchRequest ────────────────────────────────────────────────────


def test_file_search_request_rejects_empty_query() -> None:
    with pytest.raises(ValidationError):
        FileSearchRequest(query="")


# ── NoteHistoryCreateRequest ─────────────────────────────────────────────


def test_note_history_request_requires_destination_path() -> None:
    with pytest.raises(ValidationError):
        NoteHistoryCreateRequest(file_name="note.md", destination_path="")


def test_note_history_request_source_files_defaults_empty() -> None:
    req = NoteHistoryCreateRequest(file_name="note.md", destination_path="/tmp/note.md")
    assert req.source_files == []
    assert req.category_name is None
