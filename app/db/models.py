"""
SQLite database models (SQLModel ORM).

Tables:
  • app_settings      — key-value application settings
  • categories        — user-defined classification buckets
  • files             — scanned file metadata
  • file_analysis     — AI summary + rename suggestion per file
  • category_scores   — AI classification score per file×category
  • history_logs      — audit trail of every action
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, Relationship, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> str:
    return str(uuid.uuid4())


# ── App Settings ─────────────────────────────────────────────────────────


class AppSetting(SQLModel, table=True):
    __tablename__ = "app_settings"  # type: ignore[assignment]

    key: str = Field(primary_key=True)
    value: Optional[str] = Field(default=None)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


# ── Categories ───────────────────────────────────────────────────────────


class Category(SQLModel, table=True):
    __tablename__ = "categories"  # type: ignore[assignment]

    id: str = Field(default_factory=_new_uuid, primary_key=True)
    name: str = Field(nullable=False, unique=True)
    description: str = Field(default="", nullable=False)
    color: str = Field(default="#6366f1", max_length=7, nullable=False)
    destination_path: Optional[str] = Field(default=None)
    is_path_manual: bool = Field(default=False, nullable=False)  # True when user set path manually
    embedding: Optional[str] = Field(default=None)  # JSON-serialised float list
    is_default: bool = Field(default=False, nullable=False)  # Seeded by system
    is_active: bool = Field(default=True, nullable=False)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)

    # relationships
    scores: list["CategoryScore"] = Relationship(
        back_populates="category",
        cascade_delete=True,
    )


# ── Files ────────────────────────────────────────────────────────────────


class File(SQLModel, table=True):
    __tablename__ = "files"  # type: ignore[assignment]

    id: str = Field(default_factory=_new_uuid, primary_key=True)
    original_path: str = Field(nullable=False, unique=True)
    hash: str = Field(max_length=64, nullable=False)
    size: int = Field(nullable=False)
    extension: str = Field(max_length=32, nullable=False)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)

    # relationships
    analysis: Optional["FileAnalysis"] = Relationship(
        back_populates="file",
        cascade_delete=True,
    )
    scores: list["CategoryScore"] = Relationship(
        back_populates="file",
        cascade_delete=True,
    )
    history: list["HistoryLog"] = Relationship(
        back_populates="file",
        cascade_delete=True,
    )


# ── File Analysis ────────────────────────────────────────────────────────


class FileAnalysis(SQLModel, table=True):
    __tablename__ = "file_analysis"  # type: ignore[assignment]

    id: str = Field(default_factory=_new_uuid, primary_key=True)
    file_id: str = Field(
        foreign_key="files.id",
        nullable=False,
        unique=True,
    )
    summary: Optional[str] = Field(default=None)
    suggested_names: Optional[str] = Field(default=None)  # JSON-serialised list[str]
    categories_hash: Optional[str] = Field(default=None)  # MD5 of active category semantics at analysis time
    processed_at: datetime = Field(default_factory=_utcnow, nullable=False)

    # relationships
    file: Optional["File"] = Relationship(back_populates="analysis")


# ── Category Scores ──────────────────────────────────────────────────────


class CategoryScore(SQLModel, table=True):
    __tablename__ = "category_scores"  # type: ignore[assignment]

    id: str = Field(default_factory=_new_uuid, primary_key=True)
    file_id: str = Field(foreign_key="files.id", nullable=False)
    category_id: str = Field(foreign_key="categories.id", nullable=False)
    score: float = Field(default=0.0, nullable=False)

    # relationships
    file: Optional["File"] = Relationship(back_populates="scores")
    category: Optional["Category"] = Relationship(back_populates="scores")


# ── History Logs ─────────────────────────────────────────────────────────


class HistoryLog(SQLModel, table=True):
    __tablename__ = "history_logs"  # type: ignore[assignment]

    id: str = Field(default_factory=_new_uuid, primary_key=True)
    file_id: str = Field(foreign_key="files.id", nullable=False)
    action: str = Field(max_length=64, nullable=False)
    metadata_json: Optional[str] = Field(default=None)  # JSON string
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)

    # relationships
    file: Optional["File"] = Relationship(back_populates="history")
