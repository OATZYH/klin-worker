"""
Default base path settings router.

Manage the default base path for category folders.
When the default base path is updated, all categories that were NOT
manually customised by the user (`is_path_manual=False`) have their
`destination_path` auto-updated to `{base_path}/{category.name}`.

If no categories exist yet, default categories are seeded first so the
same endpoint can be used for first-launch setup and later updates.

Mounted at: /api/settings/default-base-path
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.ai_exceptions import (
    AiCapabilityUnavailableError,
    to_service_unavailable_http_exception,
)
from app.db.models import AppSetting, Category
from app.db.session import get_db
from app.models.request import DefaultBasePathUpdate
from app.models.response import (
    CategoryResponse,
    DefaultBasePathResponse,
    OnboardingStatusResponse,
)
from app.api.settings.store import (
    SETTING_KEY_ONBOARDING_COMPLETED_AT,
    SETTING_KEY_ONBOARDING_SEEDED,
    SETTING_KEY_ONBOARDING_SEEDED_AT,
    SETTING_KEY_ONBOARDING_STARTED_AT,
    SETTING_KEY_ONBOARDING_STATUS,
    SETTING_KEY_SEED_VERSION,
    get_setting_value,
    parse_bool_setting,
    parse_datetime_setting,
    upsert_setting_value,
)
from app.services.categories.classification_service import ClassificationService
from app.services.categories.seed_service import generate_missing_embeddings, seed_default_categories

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


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


@router.get("/onboarding", response_model=OnboardingStatusResponse)
async def get_onboarding_status(
    db: AsyncSession = Depends(get_db),
) -> OnboardingStatusResponse:
    """Get first-run onboarding and seeding state."""
    status = await get_setting_value(db, SETTING_KEY_ONBOARDING_STATUS, default="pending")
    onboarding_seeded = parse_bool_setting(
        await get_setting_value(db, SETTING_KEY_ONBOARDING_SEEDED),
        default=False,
    )

    has_categories_result = await db.execute(select(Category.id).limit(1))
    has_categories = has_categories_result.scalar_one_or_none() is not None

    return OnboardingStatusResponse(
        status=status or "pending",
        onboarding_seeded=onboarding_seeded,
        started_at=parse_datetime_setting(
            await get_setting_value(db, SETTING_KEY_ONBOARDING_STARTED_AT)
        ),
        seeded_at=parse_datetime_setting(
            await get_setting_value(db, SETTING_KEY_ONBOARDING_SEEDED_AT)
        ),
        completed_at=parse_datetime_setting(
            await get_setting_value(db, SETTING_KEY_ONBOARDING_COMPLETED_AT)
        ),
        should_seed_defaults=not has_categories,
    )


@router.put("/default-base-path", response_model=DefaultBasePathResponse)
async def set_default_base_path(
    body: DefaultBasePathUpdate,
    db: AsyncSession = Depends(get_db),
) -> DefaultBasePathResponse:
    """
    Set (or update) the default base path for category folders.

    If no categories exist yet, seed the default categories first.
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

    started_at = await get_setting_value(db, SETTING_KEY_ONBOARDING_STARTED_AT)
    if not started_at:
        await upsert_setting_value(db, SETTING_KEY_ONBOARDING_STARTED_AT, _utcnow_iso())

    onboarding_seeded = await get_setting_value(db, SETTING_KEY_ONBOARDING_SEEDED)
    if onboarding_seeded is None:
        await upsert_setting_value(db, SETTING_KEY_ONBOARDING_SEEDED, "false")

    await upsert_setting_value(db, SETTING_KEY_ONBOARDING_STATUS, "base_path_set")
    await upsert_setting_value(db, SETTING_KEY_SEED_VERSION, "1")

    await db.flush()

    # ── Seed defaults on first run ─────────────────────────────────
    any_result = await db.execute(select(Category).limit(1))
    has_categories = any_result.scalar_one_or_none() is not None
    seeded = 0

    if not has_categories:
        try:
            from app.main import get_rag_service

            rag = get_rag_service()
            classifier = ClassificationService(rag)

            seeded = await seed_default_categories(db)
            await db.flush()
            if seeded > 0:
                embedded = await generate_missing_embeddings(db, classifier)
                if embedded != seeded:
                    raise RuntimeError(
                        "Failed to generate embeddings for all seeded categories."
                    )
                logger.info(
                    "Default base path set on empty category table — seeded %d default categories.",
                    seeded,
                )
                await upsert_setting_value(
                    db,
                    SETTING_KEY_ONBOARDING_SEEDED_AT,
                    _utcnow_iso(),
                )
                await upsert_setting_value(db, SETTING_KEY_ONBOARDING_SEEDED, "true")
        except AiCapabilityUnavailableError as exc:
            raise to_service_unavailable_http_exception(exc) from exc

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

    # ── Generate embeddings if AI services are ready ────────────────
    if seeded == 0:
        try:
            from app.main import get_rag_service

            rag = get_rag_service()
            if rag.is_ready:
                classifier = ClassificationService(rag)
                embedded = await generate_missing_embeddings(db, classifier)
                if embedded > 0:
                    logger.info("Generated embeddings for %d categories.", embedded)
        except Exception:
            logger.debug(
                "Skipping embedding generation — AI services not ready.",
                exc_info=True,
            )

    logger.info(
        "Default base path set to '%s' — %d categories auto-updated.",
        base_path,
        updated_count,
    )

    completed_at = await get_setting_value(db, SETTING_KEY_ONBOARDING_COMPLETED_AT)
    if not completed_at:
        await upsert_setting_value(
            db,
            SETTING_KEY_ONBOARDING_COMPLETED_AT,
            _utcnow_iso(),
        )
    await upsert_setting_value(db, SETTING_KEY_ONBOARDING_STATUS, "completed")
    await db.flush()

    return DefaultBasePathResponse(
        default_base_path=base_path,
        updated_count=updated_count,
        updated_categories=updated_categories,
    )
