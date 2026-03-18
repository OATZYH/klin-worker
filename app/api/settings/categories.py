"""
Categories API router.

Canonical CRUD + batch endpoints for user-defined categories.
Mounted at: /api/settings/categories

Current API contract:
    • Request field names: `enabled`, `folder_path`
    • Response includes `learning` (bool: has AI classified at least one file?)
    • POST and PATCH return 201/204 with no response body
    • POST /batch supports bulk creation
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.ai_exceptions import (
    AiCapabilityUnavailableError,
    to_service_unavailable_http_exception,
)
from app.db.models import Category, CategoryScore
from app.db.session import get_db
from app.models.request import BatchCategoryCreate, CategoryCreate, CategoryUpdate
from app.models.response import CategoryResponse
from app.services.categories.category_embedding_text import build_category_embedding_text
from app.services.categories.classification_service import ClassificationService
from app.services.ai.rag_service import RagService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/categories", tags=["categories"])


# ── Dependency Injection ─────────────────────────────────────────────────


def _get_rag() -> RagService:
    from app.main import get_rag_service

    return get_rag_service()


def _get_classifier(rag: RagService = Depends(_get_rag)) -> ClassificationService:
    return ClassificationService(rag)


# ── Helpers ──────────────────────────────────────────────────────────────


def _to_response(cat: Category, learning: bool = False) -> CategoryResponse:
    return CategoryResponse(
        id=cat.id,
        name=cat.name,
        description=cat.description,
        color=cat.color,
        enabled=cat.is_active,
        folder_path=cat.destination_path,
        learning=learning,
        is_auto_description=cat.is_auto_description,
        updated_at=cat.updated_at,
    )


async def _generate_embedding(
    cat: Category,
    classifier: ClassificationService,
) -> str:
    """Generate embedding JSON string for a category."""
    embed_text = build_category_embedding_text(cat)
    embedding_vec = await classifier.generate_category_embedding(embed_text)
    if not embedding_vec:
        raise RuntimeError("Embedding generation returned no vector.")
    return json.dumps(embedding_vec)


# ── Routes ───────────────────────────────────────────────────────────────


@router.get("", response_model=list[CategoryResponse])
async def list_categories(
    active_only: bool = True,
    db: AsyncSession = Depends(get_db),
) -> list[CategoryResponse]:
    """List all categories (optionally only active ones)."""
    stmt = select(Category).order_by(Category.name)
    if active_only:
        stmt = stmt.where(Category.is_active.is_(True))  # type: ignore[union-attr]
    result = await db.execute(stmt)
    categories = result.scalars().all()

    cat_ids = [c.id for c in categories]
    learning_map: dict[str, bool] = {}
    if cat_ids:
        count_stmt = (
            select(CategoryScore.category_id, func.count(CategoryScore.id))  # type: ignore[arg-type]
            .where(CategoryScore.category_id.in_(cat_ids))  # type: ignore[union-attr]
            .group_by(CategoryScore.category_id)
        )
        count_result = await db.execute(count_stmt)
        for cat_id, cnt in count_result.all():
            learning_map[cat_id] = cnt > 0

    return [_to_response(c, learning=learning_map.get(c.id, False)) for c in categories]


@router.post("", status_code=201)
async def create_category(
    body: CategoryCreate,
    db: AsyncSession = Depends(get_db),
    classifier: ClassificationService = Depends(_get_classifier),
) -> Response:
    """Create a new category and generate its embedding. No response body."""
    existing = await db.execute(select(Category).where(Category.name == body.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Category '{body.name}' already exists.")

    cat = Category(
        name=body.name,
        description=body.description,
        is_active=body.enabled,
        destination_path=body.folder_path,
        color=body.color or "#6366f1",
        is_auto_description=body.is_auto_description,
    )
    try:
        cat.embedding = await _generate_embedding(cat, classifier)
    except AiCapabilityUnavailableError as exc:
        raise to_service_unavailable_http_exception(exc) from exc

    db.add(cat)
    await db.flush()

    logger.info("Created category: %s (embedding=%s)", cat.name, cat.embedding is not None)
    return Response(status_code=201)


@router.get("/{category_id}", response_model=CategoryResponse)
async def get_category(
    category_id: str,
    db: AsyncSession = Depends(get_db),
) -> CategoryResponse:
    """Get a single category by ID."""
    cat = await db.get(Category, category_id)
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found.")

    count_result = await db.execute(
        select(func.count(CategoryScore.id)).where(  # type: ignore[arg-type]
            CategoryScore.category_id == category_id
        )
    )
    learning = count_result.scalar_one() > 0

    return _to_response(cat, learning=learning)


@router.patch("/{category_id}", status_code=204)
async def update_category(
    category_id: str,
    body: CategoryUpdate,
    db: AsyncSession = Depends(get_db),
    classifier: ClassificationService = Depends(_get_classifier),
) -> Response:
    """Update a category. No response body."""
    cat = await db.get(Category, category_id)
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found.")

    need_re_embed = False
    update_data = body.model_dump(exclude_unset=True)

    field_map = {"enabled": "is_active", "folder_path": "destination_path"}

    for field, value in update_data.items():
        db_field = field_map.get(field, field)
        if field == "folder_path" and value is not None:
            cat.is_path_manual = True
        if field == "description" and value is not None:
            cat.is_auto_description = False  # User manually edited — clear auto-gen flag
        setattr(cat, db_field, value)
        if db_field in ("name", "description"):
            need_re_embed = True

    if need_re_embed:
        try:
            cat.embedding = await _generate_embedding(cat, classifier)
        except AiCapabilityUnavailableError as exc:
            raise to_service_unavailable_http_exception(exc) from exc

    await db.flush()
    logger.info("Updated category: %s (re-embed=%s)", cat.name, need_re_embed)
    return Response(status_code=204)


@router.delete("/{category_id}", status_code=204)
async def delete_category(
    category_id: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a category."""
    cat = await db.get(Category, category_id)
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found.")
    await db.delete(cat)
    logger.info("Deleted category: %s", cat.name)


@router.post("/batch", status_code=201)
async def batch_create_categories(
    body: BatchCategoryCreate,
    db: AsyncSession = Depends(get_db),
    classifier: ClassificationService = Depends(_get_classifier),
) -> Response:
    """Batch create categories. No response body."""
    existing_rows = await db.execute(select(Category.name))
    existing_names: set[str] = {name for name, in existing_rows.all()}

    created = 0
    for item in body.categories:
        if item.name in existing_names:
            logger.info("Batch: skipping duplicate category '%s'", item.name)
            continue

        cat = Category(
            name=item.name,
            description=item.description,
            is_active=item.enabled,
            destination_path=item.folder_path,
            color=item.color or "#6366f1",
            is_auto_description=item.is_auto_description,
        )
        # Skip embedding for folder-imported categories (is_auto_description=True).
        # generate_missing_embeddings() will fill them in when the AI model is ready.
        if not item.is_auto_description:
            try:
                cat.embedding = await _generate_embedding(cat, classifier)
            except AiCapabilityUnavailableError as exc:
                raise to_service_unavailable_http_exception(exc) from exc
        db.add(cat)
        existing_names.add(item.name)
        created += 1

    await db.flush()
    logger.info("Batch created %d categories", created)

    return Response(status_code=201)
