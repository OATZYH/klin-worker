"""
Category Seed Service — default categories for file classification.

Seeds default categories into the database on first launch.
Each category stores its full semantic meaning in `description`, which
may contain both natural language and keyword-style phrases.

Seed categories are marked with `is_default=True`. They are only
inserted when no default categories exist yet (idempotent).

When first-run setup sets the default base path, the caller should:
    1. Call `seed_default_categories(db)` to ensure rows exist.
    2. Call `generate_missing_embeddings(db, classifier)` once RAG is
       ready, so every category has a precomputed embedding vector.
"""

import json
import logging
from typing import Any

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import Category
from app.services.categories.category_embedding_text import build_category_embedding_text

logger = logging.getLogger(__name__)

# ── Category Definitions ─────────────────────────────────────────────────


def _compose_description(summary: str, keyword_phrases: str) -> str:
    """Combine prose and keyword phrases into one description field."""
    return f"{summary}\n{keyword_phrases}"

DEFAULT_CATEGORIES: list[dict[str, Any]] = [
    {
        "name": "Photos & Media",
        "description": _compose_description(
            "Photos, videos, screenshots, and other media files.",
            "photo, picture, image, screenshot, wallpaper, album, selfie, video, clip, recording, media file, camera photo, gallery, รูปภาพ, ภาพถ่าย, สกรีนช็อต, วิดีโอ, อัลบั้ม, กล้อง, .jpg, .jpeg, .png, .heic, .gif, .mp4, .mov, .avi, .mkv",
        ),
        "color": "#06b6d4",  # cyan
    },
    {
        "name": "Creative Projects",
        "description": _compose_description(
            "Creative and artistic works including writing, design, media production, and creative output.",
            "creative writing, stories, poems, scripts, screenplays, design briefs, moodboards, portfolios, illustrations, video projects, music composition, branding, content creation, งานสร้างสรรค์, งานศิลปะ, นิยาย, บทกวี, บทภาพยนตร์, โปรเจคออกแบบ, พอร์ตโฟลิโอ, ภาพประกอบ, ดนตรี, .psd, .ai, .blend, .fig, .sketch",
        ),
        "color": "#ec4899",  # pink
    },
    {
        "name": "Work & Business",
        "description": _compose_description(
            "Professional work documents, business files, and project planning materials.",
            "project plan, meeting notes, presentation, proposal, company profile, business strategy, roadmap, sprint planning, OKR, KPI, client deliverable, internal document, report, documentation, เอกสารบริษัท, แผนงาน, โน้ตประชุม, โปรเจคงาน, ข้อเสนอ, กลยุทธ์ธุรกิจ, .pptx, .xlsx, .docx, .pdf",
        ),
        "color": "#3b82f6",  # blue
    },
    {
        "name": "Finance & Invoices",
        "description": _compose_description(
            "Financial records, billing documents, and payment information.",
            "invoice, receipt, billing statement, bank statement, transaction history, tax document, payment confirmation, credit card statement, expense report, ใบเสร็จ, ใบกำกับภาษี, รายการเดินบัญชี, ภาษี, รายรับรายจ่าย, .pdf, .xlsx, .csv",
        ),
        "color": "#22c55e",  # green
    },
    {
        "name": "Legal & Contracts",
        "description": _compose_description(
            "Legal agreements, contracts, and official documents.",
            "contract, agreement, NDA, terms and conditions, legal notice, license agreement, court document, compliance document, สัญญา, ข้อตกลง, เอกสารกฎหมาย, หนังสือแจ้ง, ข้อกำหนด",
        ),
        "color": "#64748b",  # slate
    },
    {
        "name": "Education & Research",
        "description": _compose_description(
            "Learning materials, academic papers, and research documents.",
            "lecture notes, study material, textbook, syllabus, assignment, research paper, journal article, thesis draft, dataset description, โน้ตเรียน, หนังสือเรียน, รายงาน, งานวิจัย, วิทยานิพนธ์, บทความวิชาการ",
        ),
        "color": "#f59e0b",  # amber
    },
    {
        "name": "Personal",
        "description": _compose_description(
            "Private personal files, identification, and daily life documents.",
            "personal letter, diary, journal, private note, family document, personal record, ID card copy, certificate, บันทึกส่วนตัว, จดหมาย, เอกสารส่วนตัว, ครอบครัว",
        ),
        "color": "#a855f7",  # purple
    },
    {
        "name": "Travel",
        "description": _compose_description(
            "Travel bookings, itineraries, and travel documents.",
            "flight ticket, boarding pass, hotel reservation, itinerary, visa document, travel insurance, ตั๋วเครื่องบิน, แผนการเดินทาง, จองโรงแรม, วีซ่า",
        ),
        "color": "#0ea5e9",  # sky
    },
    {
        "name": "Health & Medical",
        "description": _compose_description(
            "Medical reports, prescriptions, and healthcare documents.",
            "medical report, lab result, prescription, doctor appointment, treatment plan, hospital bill, ผลตรวจ, ใบสั่งยา, ใบรับรองแพทย์, โรงพยาบาล",
        ),
        "color": "#ef4444",  # red
    },
    {
        "name": "General Documents",
        "description": _compose_description(
            "Unclassified or mixed documents that do not fit another category.",
            "miscellaneous document, general file, uncategorized, เอกสารทั่วไป",
        ),
        "color": "#78716c",  # stone
    },
]

# ── Public API ───────────────────────────────────────────────────────────


async def seed_default_categories(db: AsyncSession) -> int:
    """
    Insert default categories if the table is empty or no defaults exist.

    Triggers on first run (empty table) or if defaults were deleted.
    Returns the number of categories inserted (0 if already seeded).
    """
    # Check if ANY categories exist in the table
    any_result = await db.execute(select(Category).limit(1))
    has_any = any_result.scalar_one_or_none() is not None

    if has_any:
        # Table is not empty — check if default categories specifically exist
        default_result = await db.execute(
            select(Category).where(Category.is_default.is_(True)).limit(1)  # type: ignore[union-attr]
        )
        if default_result.scalar_one_or_none() is not None:
            logger.debug("Default categories already seeded — skipping.")
            return 0
        logger.info("Categories exist but no defaults found — seeding defaults.")
    else:
        logger.info("Categories table is empty — seeding defaults for first run.")

    inserted = 0
    for defn in DEFAULT_CATEGORIES:
        # Guard: skip if a category with the same name exists (user-created)
        existing = await db.execute(
            select(Category).where(Category.name == defn["name"])
        )
        if existing.scalar_one_or_none() is not None:
            logger.debug("Category '%s' already exists — skipping.", defn["name"])
            continue

        cat = Category(
            name=defn["name"],
            description=defn["description"],
            color=defn["color"],
            is_default=True,
        )
        db.add(cat)
        inserted += 1

    if inserted:
        await db.flush()
        logger.info("Seeded %d default categories.", inserted)

    return inserted


async def generate_missing_embeddings(
    db: AsyncSession,
    classifier: Any,
) -> int:
    """
    Generate embeddings for categories that don't have one yet.

    Uses `name + description` as the embedding source text.
    The description may already contain keyword phrases.

    Returns the number of embeddings generated.
    """
    result = await db.execute(
        select(Category).where(
            Category.is_active.is_(True),  # type: ignore[union-attr]
            Category.embedding.is_(None),  # type: ignore[union-attr]
        )
    )
    categories = result.scalars().all()

    if not categories:
        logger.debug("All active categories already have embeddings.")
        return 0

    count = 0
    for cat in categories:
        embed_text = build_category_embedding_text(cat)
        vec = await classifier.generate_category_embedding(embed_text)
        if vec:
            cat.embedding = json.dumps(vec)
            count += 1
            logger.debug("Generated embedding for category '%s'.", cat.name)
        else:
            logger.warning("Failed to generate embedding for '%s'.", cat.name)

    if count:
        await db.flush()
        logger.info("Generated embeddings for %d categories.", count)

    return count

