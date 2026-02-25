"""
Request models for the organize API.
"""

from pydantic import BaseModel, Field


class OrganizeRules(BaseModel):
    """Optional rules that control analysis behaviour."""

    check_dup: bool = Field(
        default=True,
        description="Enable semantic duplicate detection.",
    )
    allow_rename: bool = Field(
        default=True,
        description="Allow the system to suggest renames.",
    )


class OrganizeRequest(BaseModel):
    """
    POST /api/organize request body.

    `organize` contains **absolute** file paths sent from the Tauri frontend.
    No file upload — only local path references.
    """

    organize: list[str] = Field(
        ...,
        min_length=1,
        description="List of absolute file paths to analyse.",
        examples=[["C:\\Users\\oatln\\Downloads\\file1.pdf"]],
    )
    rules: OrganizeRules = Field(
        default_factory=OrganizeRules,
        description="Optional analysis rules.",
    )
