"""Voyager integration service.

Keeps Voyager setup separate from app bootstrap so it can be configured
through settings without editing app/main.py.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

import fastapi_voyager.voyager as voyager_module
from fastapi import FastAPI
from fastapi_voyager import create_voyager
from pydantic_resolve import Entity, ErDiagram, Relationship

from app.core.config import settings
from app.db.models import (
    AppSetting,
    Category,
    CategoryScore,
    File,
    FileAnalysis,
    HistoryLog,
    SystemLog,
)

logger = logging.getLogger(__name__)

_INITIAL_PAGE_POLICIES: set[str] = {"first", "full", "empty"}
_IS_VOYAGER_PATCHED = False


def _build_er_diagram() -> ErDiagram:
    """Build ER diagram metadata for Voyager from SQLModel entities."""
    return ErDiagram(
        configs=[
            Entity(kls=AppSetting),
            Entity(
                kls=Category,
                relationships=[
                    Relationship(field="id", target_kls=list[CategoryScore]),
                ],
            ),
            Entity(
                kls=File,
                relationships=[
                    Relationship(field="id", target_kls=FileAnalysis),
                    Relationship(field="id", target_kls=list[CategoryScore]),
                    Relationship(field="id", target_kls=list[HistoryLog]),
                ],
            ),
            Entity(
                kls=FileAnalysis,
                relationships=[
                    Relationship(field="file_id", target_kls=File),
                ],
            ),
            Entity(
                kls=CategoryScore,
                relationships=[
                    Relationship(field="file_id", target_kls=File),
                    Relationship(field="category_id", target_kls=Category),
                ],
            ),
            Entity(
                kls=HistoryLog,
                relationships=[
                    Relationship(field="file_id", target_kls=File),
                ],
            ),
            Entity(kls=SystemLog),
        ]
    )


def _patch_fastapi_voyager_core_type_handling() -> None:
    """Prevent Voyager crashes on routes without class-based response models."""
    global _IS_VOYAGER_PATCHED

    if _IS_VOYAGER_PATCHED:
        return

    original_get_core_types = voyager_module.get_core_types

    def _safe_get_core_types(tp: object) -> tuple[type, ...]:
        core_types = original_get_core_types(tp)
        return tuple(item for item in core_types if isinstance(item, type))

    voyager_module.get_core_types = _safe_get_core_types
    _IS_VOYAGER_PATCHED = True


def _get_initial_page_policy() -> Literal["first", "full", "empty"]:
    """Normalize voyager initial page policy from settings."""
    raw_policy = settings.voyager_initial_page_policy.strip().lower()
    if raw_policy not in _INITIAL_PAGE_POLICIES:
        logger.warning(
            "Invalid KLIN_VOYAGER_INITIAL_PAGE_POLICY='%s'. Falling back to 'first'.",
            settings.voyager_initial_page_policy,
        )
        return "first"

    return raw_policy  # type: ignore[return-value]


def create_voyager_subapp(target_app: FastAPI) -> Any:
    """Create a fully configured Voyager sub-application."""
    _patch_fastapi_voyager_core_type_handling()

    return create_voyager(
        target_app,
        er_diagram=_build_er_diagram(),
        module_color=settings.voyager_module_colors or None,
        module_prefix=settings.voyager_module_prefix,
        swagger_url=settings.voyager_swagger_url,
        online_repo_url=settings.voyager_online_repo_url,
        initial_page_policy=_get_initial_page_policy(),
        enable_pydantic_resolve_meta=settings.voyager_enable_pydantic_resolve_meta,
    )


def mount_voyager(target_app: FastAPI) -> None:
    """Mount Voyager if enabled in settings."""
    if not settings.voyager_enabled:
        logger.info("Voyager is disabled by configuration.")
        return

    mount_path = settings.voyager_mount_path.strip() or "/voyager"
    if not mount_path.startswith("/"):
        mount_path = f"/{mount_path}"

    target_app.mount(mount_path, create_voyager_subapp(target_app))
    logger.info("Voyager mounted at %s", mount_path)
