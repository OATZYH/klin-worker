"""
Response models for the organize API.
"""

from typing import Optional

from pydantic import BaseModel, Field


class FileScanResult(BaseModel):
    """Metadata extracted by ScannerService for a single file."""

    original_path: str
    file_name: str
    extension: str
    size_bytes: int
    sha256: str
    exists: bool
    error: Optional[str] = None


class FileAnalysisResult(BaseModel):
    """
    Per-file analysis returned to the frontend.

    The backend only *suggests* actions — it never moves, renames, or deletes.
    """

    original_path: str
    status: str = Field(
        description="'ok' | 'duplicate' | 'error'",
        examples=["ok"],
    )
    duplicate_of: Optional[str] = Field(
        default=None,
        description="Path of the original file if this is a semantic duplicate.",
    )
    suggested_name: Optional[str] = Field(
        default=None,
        description="AI-suggested rename (only when allow_rename is True).",
    )
    suggested_category: Optional[str] = Field(
        default=None,
        description="Semantic category from RAG analysis.",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence score of the analysis.",
    )
    metadata: Optional[FileScanResult] = Field(
        default=None,
        description="Raw scanner metadata for transparency.",
    )


class OrganizeSummary(BaseModel):
    """High-level summary of the analysis run."""

    total_files: int
    scanned_ok: int
    duplicates_found: int
    rename_suggestions: int
    errors: int


class OrganizeResponse(BaseModel):
    """
    Response for POST /api/organize.

    Returns an action plan — no side effects on the file system.
    """

    summary: OrganizeSummary
    files: list[FileAnalysisResult]
