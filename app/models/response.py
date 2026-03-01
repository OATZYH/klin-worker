"""
Response models for the Klin-Worker API.
"""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Scanner ──────────────────────────────────────────────────────────────


class FileScanResult(BaseModel):
    """Metadata extracted by ScannerService for a single file."""

    original_path: str
    file_name: str
    extension: str
    size_bytes: int
    sha256: str
    exists: bool
    error: Optional[str] = None


# ── Category ─────────────────────────────────────────────────────────────


class CategoryResponse(BaseModel):
    """Single category returned to the frontend."""

    id: str
    name: str
    description: str
    keywords_text: Optional[str] = None
    color: str
    destination_path: Optional[str] = None
    is_default: bool = False
    is_active: bool
    created_at: datetime
    updated_at: datetime


class CategoryScoreResponse(BaseModel):
    """A single category ↔ file score."""

    category_id: str
    name: str
    score: float


# ── Organize ─────────────────────────────────────────────────────────────


class FileAnalysisResponse(BaseModel):
    """AI-generated analysis of a single file."""

    summary: Optional[str] = None
    suggested_name: Optional[str] = None


class TopCategoryResponse(BaseModel):
    """The winning category for a file."""

    category_id: str
    name: str
    score: float
    destination_path: Optional[str] = None


class OrganizeFileResult(BaseModel):
    """Per-file result returned from POST /api/organize."""

    filepath: str
    file_id: str
    analysis: FileAnalysisResponse
    categories: list[CategoryScoreResponse]
    top_category: Optional[TopCategoryResponse] = None
    error: Optional[str] = None


class OrganizeResponse(BaseModel):
    """Response for POST /api/organize."""

    results: list[OrganizeFileResult]


# ── History ──────────────────────────────────────────────────────────────


class HistoryLogResponse(BaseModel):
    """Single audit log entry."""

    id: str
    file_id: str
    action: str
    metadata: Optional[dict[str, Any]] = None
    created_at: datetime
