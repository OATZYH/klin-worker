"""
Request models for the Klin-Worker API.
"""

from pydantic import BaseModel, Field


# ── Organize ─────────────────────────────────────────────────────────────


class OrganizeRequest(BaseModel):
    """
    POST /api/organize request body.

    `filepaths` contains **absolute** file paths sent from the Tauri frontend.
    No file upload — only local path references.
    """

    filepaths: list[str] = Field(
        ...,
        min_length=1,
        description="List of absolute file paths to analyse.",
        examples=[["/Users/sarun/Downloads/doc1.pdf"]],
    )


# ── Categories ───────────────────────────────────────────────────────────


class CategoryCreate(BaseModel):
    """Create a new user-defined category."""

    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    color: str = Field(default="#6366f1", pattern=r"^#[0-9a-fA-F]{6}$")
    destination_path: str | None = Field(
        default=None,
        description="Absolute path where files of this category should be moved.",
    )


class CategoryUpdate(BaseModel):
    """Update an existing category (partial)."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    destination_path: str | None = None
    is_active: bool | None = None
