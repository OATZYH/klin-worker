"""Tests for build_category_embedding_text."""

from __future__ import annotations

import pytest

from app.db.models import Category
from app.services.categories.category_embedding_text import build_category_embedding_text

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "name, description, expected",
    [
        ("Finance", "Invoices and receipts", "Finance\nInvoices and receipts"),
        ("Misc", "", "Misc"),
        ("  Work  ", "  meeting notes  ", "Work\nmeeting notes"),
    ],
    ids=["with_description", "without_description", "whitespace_stripped"],
)
def test_build_category_embedding_text(name: str, description: str, expected: str) -> None:
    cat = Category(name=name, description=description, color="#111111")
    assert build_category_embedding_text(cat) == expected
