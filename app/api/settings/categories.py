"""
Categories API router.

CRUD endpoints for user-defined categories.
Each category has a name, description, color, and optional destination path.
When description changes, the embedding is regenerated automatically.

Mounted at: /api/settings/categories
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import Category
from app.db.session import get_db
from app.models.request import CategoryCreate, CategoryUpdate
from app.models.response import CategoryResponse
from app.services.classification_service import ClassificationService
from app.services.rag_service import RagService
from app.services.seed_service import _build_embed_text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/categories", tags=["categories"])


# ── Dependency Injection ─────────────────────────────────────────────────


def _get_rag() -> RagService:
    from app.main import get_rag_service

    return get_rag_service()


def _get_classifier(rag: RagService = Depends(_get_rag)) -> ClassificationService:
    return ClassificationService(rag)


# ── Helpers ──────────────────────────────────────────────────────────────


def _to_response(cat: Category) -> CategoryResponse:
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


@router.get("", response_model=list[CategoryResponse])
async def list_categories(
    active_only: bool = True,
    db: AsyncSession = Depends(get_db),
) -> list[CategoryResponse]:
    """List all categories (optionally only active ones)."""
    stmt = select(Category).order_by(Category.name)
    if active_only:
        stmt = stmt.where(Category.is_active.is_(True))
    result = await db.execute(stmt)
    return [_to_response(c) for c in result.scalars().all()]


@router.post("", response_model=CategoryResponse, status_code=201)
async def create_category(
    body: CategoryCreate,
    db: AsyncSession = Depends(get_db),
    classifier: ClassificationService = Depends(_get_classifier),
) -> CategoryResponse:
    """Create a new category and generate its embedding."""
    # Check uniqueness
    existing = await db.execute(select(Category).where(Category.name == body.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Category '{body.name}' already exists.")

    # Generate embedding from name + description + keywords
    embedding = None
    cat = Category(
        name=body.name,
        description=body.description,
        keywords_text=body.keywords_text,
        color=body.color,
        destination_path=body.destination_path,
    )
    embed_text = _build_embed_text(cat)
    embedding_vec = await classifier.generate_category_embedding(embed_text)
    if embedding_vec:
        embedding = json.dumps(embedding_vec)

    cat.embedding = embedding
    db.add(cat)
    await db.flush()

    logger.info("Created category: %s (embedding=%s)", cat.name, embedding is not None)
    return _to_response(cat)


@router.get("/{category_id}", response_model=CategoryResponse)
async def get_category(
    category_id: str,
    db: AsyncSession = Depends(get_db),
) -> CategoryResponse:
    """Get a single category by ID."""
    cat = await db.get(Category, category_id)
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found.")
    return _to_response(cat)


@router.patch("/{category_id}", response_model=CategoryResponse)
async def update_category(
    category_id: str,
    body: CategoryUpdate,
    db: AsyncSession = Depends(get_db),
    classifier: ClassificationService = Depends(_get_classifier),
) -> CategoryResponse:
    """
    Update a category.

    If `name` or `description` changes, the embedding is regenerated.
    """
    cat = await db.get(Category, category_id)
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found.")

    need_re_embed = False
    update_data = body.model_dump(exclude_unset=True)

    # If user explicitly sets destination_path, mark as manual
    if "destination_path" in update_data and update_data["destination_path"] is not None:
        cat.is_path_manual = True

    for field, value in update_data.items():
        setattr(cat, field, value)
        if field in ("name", "description", "keywords_text"):
            need_re_embed = True

    # Regenerate embedding when text changes
    if need_re_embed:
        embed_text = _build_embed_text(cat)
        embedding_vec = await classifier.generate_category_embedding(embed_text)
        if embedding_vec:
            cat.embedding = json.dumps(embedding_vec)

    await db.flush()
    logger.info("Updated category: %s (re-embed=%s)", cat.name, need_re_embed)
    return _to_response(cat)


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
