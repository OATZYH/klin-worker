"""
Request models for the Klin-Worker API.
"""

from pydantic import BaseModel, Field, model_validator


# ── Organize ─────────────────────────────────────────────────────────────


class OrganizeRequest(BaseModel):
    """
    POST /api/organize request body.

    `file_paths` contains **absolute** file paths sent from the Tauri frontend.
    No file upload — only local path references.
    """

    file_paths: list[str] = Field(
        ...,
        min_length=1,
        description="List of absolute file paths to analyse.",
        examples=[["/Users/sarun/Downloads/doc1.pdf"]],
    )
    force: bool = Field(
        default=False,
        description="Force re-processing even if file is unchanged and cached.",
    )
class ApplySelectedCategoryRequest(BaseModel):
    """Selected category snapshot sent back from the organize response."""

    id: str = Field(..., min_length=1, description="Category id selected by the user.")
    name: str = Field(..., min_length=1, description="Category name shown in the organize response.")
    score: float = Field(
        ...,
        ge=0,
        le=100,
        description="Confidence percentage from the organize response (0-100).",
    )


class ApplyOrganizeDecisionRequest(BaseModel):
    """POST /api/organize/apply request body."""

    file_id: str = Field(..., min_length=1, description="Existing file id returned by organize.")
    selected_name: str | None = Field(
        default=None,
        min_length=1,
        description="User-selected final file name. If missing an extension, the current extension is preserved.",
    )
    selected_category: ApplySelectedCategoryRequest | None = Field(
        default=None,
        description="Category selected by the user, including the score shown in organize.",
    )

    @model_validator(mode="after")
    def _validate_has_action(self) -> "ApplyOrganizeDecisionRequest":
        if not self.selected_name and not self.selected_category:
            raise ValueError("At least one of selected_name or selected_category must be provided.")
        return self


# ── Categories ───────────────────────────────────────────────────────────


class CategoryCreate(BaseModel):
    """Create a new user-defined category."""

    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(
        default="",
        max_length=2000,
        description="Free-form category meaning. Can contain natural language plus comma-separated keywords or phrases.",
    )
    enabled: bool = Field(default=True)
    folder_path: str | None = Field(
        default=None,
        description="Absolute path where files of this category should be moved.",
    )
    color: str | None = Field(
        default=None,
        pattern=r"^#[0-9a-fA-F]{6}$",
        description="Hex color code, e.g. #6366f1. Defaults to #6366f1 if not provided.",
    )


class CategoryUpdate(BaseModel):
    """Update an existing category (partial)."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(
        default=None,
        max_length=2000,
        description="Free-form category meaning. Can contain natural language plus comma-separated keywords or phrases.",
    )
    enabled: bool | None = None
    folder_path: str | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")


class BatchCategoryCreate(BaseModel):
    """Batch create categories."""

    categories: list[CategoryCreate] = Field(..., min_length=1)


# ── Settings ───────────────────────────────────────────────────────────


class DefaultBasePathUpdate(BaseModel):
    """PUT /api/settings/default-base-path request body."""

    default_base_path: str = Field(
        ...,
        min_length=1,
        description="Absolute path to the default folder for category sub-folders.",
        examples=["/Users/sarun/KlinFiles"],
    )


class InitialBasePathRequest(BaseModel):
    """PUT /api/settings/initial-base-path request body.

    Called by Tauri on first launch to set the OS-specific base path
    *before* category seeding happens.
    """

    default_base_path: str = Field(
        ...,
        min_length=1,
        description="Absolute path for the default base folder (e.g. ~/Documents/KlinFiles).",
        examples=["/Users/sarun/Documents/KlinFiles"],
    )


# ── Search ───────────────────────────────────────────────────────────────


class FileSearchRequest(BaseModel):
    """POST /api/search/files request body."""

    query: str = Field(
        ...,
        min_length=1,
        description="Search keyword used to match file metadata.",
        examples=["invoice"],
    )


# ── Notes ────────────────────────────────────────────────────────────────


class NotesSummarizeRequest(BaseModel):
    """POST /api/notes/summarize request body."""

    filePaths: list[str] = Field(
        ...,
        min_length=1,
        description="Absolute file paths to summarize.",
        examples=[["/Users/sarun/Downloads/meeting-notes.txt"]],
    )
    context: str | None = Field(
        default=None,
        description="Optional user-provided context for summary generation.",
    )


class NoteHistoryCreateRequest(BaseModel):
    """POST /api/history/note request body."""

    file_name: str = Field(..., min_length=1, description="Saved note file name.")
    destination_path: str = Field(..., min_length=1, description="Absolute path of the saved note file.")
    source_files: list[str] = Field(default_factory=list, description="Optional source files used to build the note.")
    category_name: str | None = Field(default=None, description="Category name when saved via category action.")
