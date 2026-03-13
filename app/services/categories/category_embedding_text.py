"""Helpers for building category text used by embedding-related workflows."""

from app.db.models import Category


def build_category_embedding_text(category: Category) -> str:
    """Build the text blob used to generate a category embedding."""
    parts = [category.name.strip()]
    description = category.description.strip() if category.description else ""
    if description:
        parts.append(description)
    return "\n".join(parts)