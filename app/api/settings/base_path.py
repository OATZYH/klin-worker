"""
Default base path settings router.

Manage the default base path for category folders.
When the default base path is updated, all categories that were NOT
manually customised by the user (is_path_manual=False) have their
destination_path auto-updated to `{base_path}/{category.name}`.

Mounted at: /api/settings/default-base-path
"""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import AppSetting, Category
from app.db.session import get_db
from app.models.request import DefaultBasePathUpdate
from app.models.response import CategoryResponse, DefaultBasePathResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["settings"])

SETTING_KEY_BASE_PATH = "default_base_path"


# ── Helpers ──────────────────────────────────────────────────────────────


def _category_to_response(cat: Category) -> CategoryResponse:
    return CategoryResponse(
        id=cat.id,
        name=cat.name,
        description=cat.description,
        keywords_text=cat.keywords_text,
        color=cat.color,
        destination_path=cat.destination_path,
        is_path_manual=cat.is_path_manual,
        is_default=cat.is_default,
        is_active=cat.is_active,
        created_at=cat.created_at,
        updated_at=cat.updated_at,
    )


# ── Routes ───────────────────────────────────────────────────────────────


@router.get("/default-base-path", response_model=DefaultBasePathResponse)
async def get_default_base_path(
    db: AsyncSession = Depends(get_db),
) -> DefaultBasePathResponse:
    """Get the current default base path for category folders."""
    setting = await db.get(AppSetting, SETTING_KEY_BASE_PATH)
    return DefaultBasePathResponse(
        default_base_path=setting.value if setting else None,
    )


@router.put("/default-base-path", response_model=DefaultBasePathResponse)
async def set_default_base_path(
    body: DefaultBasePathUpdate,
    db: AsyncSession = Depends(get_db),
) -> DefaultBasePathResponse:
    """
    Set (or update) the default base path for category folders.

    Every category where `is_path_manual=False` will have its
    `destination_path` auto-updated to `{base_path}/{category.name}`.
    Categories whose path was manually set by the user are untouched.
    """
    base_path = body.default_base_path.rstrip("/")

    # Validate that the parent directory exists (the base itself will be created later)
    parent = Path(base_path).parent
    if not parent.exists():
        raise HTTPException(
            status_code=400,
            detail=f"Parent directory does not exist: {parent}",
        )

    # ── Upsert setting ───────────────────────────────────────────────
    setting = await db.get(AppSetting, SETTING_KEY_BASE_PATH)
    if setting:
        setting.value = base_path
    else:
        setting = AppSetting(key=SETTING_KEY_BASE_PATH, value=base_path)
        db.add(setting)

    await db.flush()

    # ── Auto-update non-manual categories ────────────────────────────
    result = await db.execute(
        select(Category).where(
            Category.is_active.is_(True),  # type: ignore[union-attr]
            Category.is_path_manual.is_(False),  # type: ignore[union-attr]
        )
    )
    categories = result.scalars().all()

    updated_count = 0
    updated_categories: list[CategoryResponse] = []
    for cat in categories:
        new_path = f"{base_path}/{cat.name}"
        cat.destination_path = new_path
        updated_count += 1
        updated_categories.append(_category_to_response(cat))

    await db.flush()

    logger.info(
        "Default base path set to '%s' — %d categories auto-updated.",
        base_path,
        updated_count,
    )

    return DefaultBasePathResponse(
        default_base_path=base_path,
        updated_count=updated_count,
        updated_categories=updated_categories,
    )
