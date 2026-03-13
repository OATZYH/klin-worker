"""
Response models for the Klin-Worker API.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ── Category ─────────────────────────────────────────────────────────────


class CategoryResponse(BaseModel):
    """Single category returned to the frontend (V3)."""

    id: str
    name: str
    description: str
    color: str
    enabled: bool
    folder_path: Optional[str] = None
    learning: bool = False
    updated_at: datetime


class CategoryScoreResponse(BaseModel):
    """A single category ↔ file score (percentage 0-100)."""

    category_id: str
    name: str
    score: float = Field(description="Confidence percentage (0-100)")


class SelectedCategoryScoreResponse(BaseModel):
    """Category selected by the user for a confirmed action."""

    id: str
    name: str
    score: float | None = Field(default=None, description="Confidence percentage (0-100)")


# ── Organize ─────────────────────────────────────────────────────────────


class FileAnalysisResponse(BaseModel):
    """AI-generated analysis of a single file."""

    suggested_names: list[str] = Field(default_factory=list)


class OrganizeFileResult(BaseModel):
    """Per-file result returned from POST /api/organize."""

    file_id: str
    analysis: FileAnalysisResponse
    categories: list[CategoryScoreResponse]
    error: Optional[str] = None


class OrganizeResponse(BaseModel):
    """Response for POST /api/organize."""

    results: dict[str, OrganizeFileResult]


class ApplyOrganizeDecisionResponse(BaseModel):
    """Minimal success response for POST /api/organize/apply."""

    success: bool = True


# ── History ──────────────────────────────────────────────────────────────


class HistoryLogResponse(BaseModel):
    """Single audit log entry (V3)."""

    id: str
    file_id: str
    action: str
    file_name: str
    category: SelectedCategoryScoreResponse | None = None
    original_path: Optional[str] = None
    new_path: Optional[str] = None
    created_at: datetime


class HistoryListResponse(BaseModel):
    """Wrapped history response for V3."""

    results: list[HistoryLogResponse]
    limit: int = 0
    offset: int = 0
    has_more: bool = False


# ── Settings ────────────────────────────────────────────────────────────


class DefaultBasePathResponse(BaseModel):
    """Response for default base path setting."""

    default_base_path: Optional[str] = None
    updated_count: int = 0
    updated_categories: list[CategoryResponse] = []


# ── Search ───────────────────────────────────────────────────────────────


class FileSearchResultItem(BaseModel):
    """Single file row returned from POST /api/search/files."""

    id: str
    file_name: str
    file_type: str
    size_bytes: int
    folder: str
    last_edited: datetime
    path: str


class FileSearchResponse(BaseModel):
    """Response for POST /api/search/files."""

    results: list[FileSearchResultItem]


# ── Notes ────────────────────────────────────────────────────────────────


class NotesSummarizeResponse(BaseModel):
    """Response for POST /api/notes/summarize."""

    summary: str
    suggested_title: str | None = None
    processing_time_ms: int | None = None
