"""
Initial base path endpoint — called once by Tauri on startup.

Sets the OS-specific base path AND triggers category seeding (if no
categories exist yet).  This replaces the old "seed at lifespan" approach
so the frontend controls *when* seeding happens and can supply the
correct platform path first.

Flow:
  1. Save `default_base_path` to `app_settings`.
  2. If no categories exist → seed defaults with `destination_path`
     already set to `{base_path}/{category.name}`.
  3. If categories exist → auto-update non-manual ones (same as
     PUT /api/settings/default-base-path).
  4. Trigger embedding generation if GGUF model + RAG are ready.
  5. Return the list of categories and whether seeding occurred.

Mounted at: /api/settings/initial-base-path
"""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import AppSetting, Category
from app.db.session import get_db
from app.models.request import InitialBasePathRequest
from app.models.response import CategoryResponse, InitialBasePathResponse
from app.services.classification_service import ClassificationService
from app.services.seed_service import generate_missing_embeddings, seed_default_categories

logger = logging.getLogger(__name__)

router = APIRouter(tags=["settings"])

SETTING_KEY_BASE_PATH = "default_base_path"


# ── Helpers ──────────────────────────────────────────────────────────────


def _category_to_response(cat: Category) -> CategoryResponse:
    return CategoryResponse(
        id=cat.id,
        name=cat.name,
        description=cat.description,
        color=cat.color,
        enabled=cat.is_active,
        folder_path=cat.destination_path,
        learning=False,
        updated_at=cat.updated_at,
    )


# ── Route ────────────────────────────────────────────────────────────────


@router.put("/initial-base-path", response_model=InitialBasePathResponse)
async def set_initial_base_path(
    body: InitialBasePathRequest,
    db: AsyncSession = Depends(get_db),
) -> InitialBasePathResponse:
    """
    Set the default base path and seed categories on first launch.

    Tauri should call this once at startup (after /health succeeds) to
    provide the OS-specific base directory.  If no categories exist yet,
    default categories are seeded with their `destination_path` already
    set to `{base_path}/{category.name}`.

    On subsequent launches the endpoint is idempotent — it updates the
    base path and refreshes non-manual category paths without re-seeding.
    """
    base_path = body.default_base_path.rstrip("/")

    # Validate parent directory
    parent = Path(base_path).parent
    if not parent.exists():
        raise HTTPException(
            status_code=400,
            detail=f"Parent directory does not exist: {parent}",
        )

    # ── 1. Upsert default_base_path in app_settings ─────────────────
    setting = await db.get(AppSetting, SETTING_KEY_BASE_PATH)
    if setting:
        setting.value = base_path
    else:
        setting = AppSetting(key=SETTING_KEY_BASE_PATH, value=base_path)
        db.add(setting)

    await db.flush()

    # ── 2. Seed or update categories ─────────────────────────────────
    categories_seeded = False

    # Check if any categories exist
    any_result = await db.execute(select(Category).limit(1))
    has_categories = any_result.scalar_one_or_none() is not None

    if not has_categories:
        # First launch — seed defaults
        seeded = await seed_default_categories(db)
        await db.flush()
        categories_seeded = seeded > 0

        if categories_seeded:
            logger.info(
                "Initial setup — seeded %d default categories.", seeded
            )

    # ── 3. Set destination_path on all non-manual categories ─────────
    result = await db.execute(
        select(Category).where(
            Category.is_active.is_(True),  # type: ignore[union-attr]
            Category.is_path_manual.is_(False),  # type: ignore[union-attr]
        )
    )
    auto_categories = result.scalars().all()

    for cat in auto_categories:
        cat.destination_path = f"{base_path}/{cat.name}"

    await db.flush()

    # ── 4. Generate embeddings if AI services are ready ──────────────
    try:
        from app.main import get_rag_service
        from app.services.llm_client import llm_client

        rag = get_rag_service()
        if rag.is_ready and llm_client.is_ready:
            classifier = ClassificationService(rag)
            embedded = await generate_missing_embeddings(db, classifier)
            if embedded > 0:
                logger.info("Generated embeddings for %d categories.", embedded)
    except Exception:
        logger.debug(
            "Skipping embedding generation — AI services not ready.",
            exc_info=True,
        )

    await db.commit()

    # ── 5. Return all active categories ──────────────────────────────
    all_result = await db.execute(
        select(Category).where(
            Category.is_active.is_(True),  # type: ignore[union-attr]
        )
    )
    all_categories = all_result.scalars().all()

    return InitialBasePathResponse(
        default_base_path=base_path,
        categories_seeded=categories_seeded,
        categories=[_category_to_response(c) for c in all_categories],
    )
