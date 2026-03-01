"""
Category Seed Service — default categories for file classification.

Seeds 12 predefined categories into the database on first boot.
Each category includes rich semantic keywords (EN + TH) so the
embedding vector captures broad meaning for classification.

Seed categories are marked with `is_default=True`.  They are only
inserted when no default categories exist yet (idempotent).

At boot time the caller should:
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

logger = logging.getLogger(__name__)

# ── Category Definitions ─────────────────────────────────────────────────
# Each entry mirrors the spec:
#   name, description, keywords_text (EN + TH + doc hints), color

DEFAULT_CATEGORIES: list[dict[str, Any]] = [
    {
        "name": "Creative Projects",
        "description": "Creative and artistic works including writing, design, media production, and personal creative output.",
        "keywords_text": (
            "creative writing, novel, story, poem, script, screenplay, design brief, "
            "moodboard, art project, portfolio, photography, illustration, video production, "
            "music composition, creative concept, artwork, storyboard, branding, content creation, "
            "artistic expression, creative draft, "
            "งานสร้างสรรค์, งานศิลปะ, นิยาย, บทกวี, บทภาพยนตร์, โปรเจคออกแบบ, แบรนดิ้ง, "
            "พอร์ตโฟลิโอ, ถ่ายภาพ, วิดีโอ, ดนตรี, ไอเดีย, "
            ".psd .ai .png .jpg .mp4 .docx .pdf .pptx"
        ),
        "color": "#ec4899",  # pink
    },
    {
        "name": "Work & Projects",
        "description": "Professional business documents, corporate files, client work, project planning.",
        "keywords_text": (
            "project plan, meeting notes, presentation, proposal, company profile, "
            "contract draft, business strategy, roadmap, sprint planning, OKR, KPI, "
            "client deliverable, invoice draft, internal document, "
            "เอกสารบริษัท, แผนงาน, โน้ตประชุม, โปรเจคงาน, ข้อเสนอ, กลยุทธ์, ลูกค้า, "
            ".pptx .xlsx .docx .pdf"
        ),
        "color": "#3b82f6",  # blue
    },
    {
        "name": "Finance & Invoices",
        "description": "Financial records, transactions, billing documents, payment confirmations.",
        "keywords_text": (
            "invoice, receipt, billing statement, bank statement, transaction history, "
            "tax document, payment confirmation, credit card statement, expense report, "
            "ใบเสร็จ, ใบกำกับภาษี, รายการเดินบัญชี, ภาษี, รายรับรายจ่าย, "
            ".pdf .xlsx .csv"
        ),
        "color": "#22c55e",  # green
    },
    {
        "name": "Legal & Contracts",
        "description": "Legal agreements, official documents, compliance papers.",
        "keywords_text": (
            "contract, agreement, NDA, terms and conditions, legal notice, "
            "license agreement, court document, compliance document, "
            "สัญญา, ข้อตกลง, เอกสารกฎหมาย, หนังสือแจ้ง, ข้อกำหนด"
        ),
        "color": "#64748b",  # slate
    },
    {
        "name": "Education & Learning",
        "description": "Learning materials, course content, academic documents.",
        "keywords_text": (
            "lecture notes, study material, textbook, syllabus, assignment, "
            "research notes, academic paper, thesis draft, "
            "โน้ตเรียน, หนังสือเรียน, รายงาน, งานวิจัย, วิทยานิพนธ์"
        ),
        "color": "#f59e0b",  # amber
    },
    {
        "name": "Personal",
        "description": "Private personal documents and memories.",
        "keywords_text": (
            "personal letter, diary, journal, private note, family document, "
            "personal record, "
            "บันทึกส่วนตัว, จดหมาย, เอกสารส่วนตัว, ครอบครัว"
        ),
        "color": "#a855f7",  # purple
    },
    {
        "name": "Travel & Vacation",
        "description": "Travel-related documents and bookings.",
        "keywords_text": (
            "flight ticket, boarding pass, hotel reservation, itinerary, "
            "visa document, travel insurance, "
            "ตั๋วเครื่องบิน, แผนการเดินทาง, จองโรงแรม, วีซ่า"
        ),
        "color": "#06b6d4",  # cyan
    },
    {
        "name": "Health & Medical",
        "description": "Medical records and healthcare-related files.",
        "keywords_text": (
            "medical report, lab result, prescription, doctor appointment, "
            "treatment plan, hospital bill, "
            "ผลตรวจ, ใบสั่งยา, ใบรับรองแพทย์, โรงพยาบาล"
        ),
        "color": "#ef4444",  # red
    },
    {
        "name": "Technology & Manuals",
        "description": "Technical documentation and system guides.",
        "keywords_text": (
            "API documentation, system architecture, user manual, troubleshooting guide, "
            "technical specification, SDK guide, "
            "คู่มือ, เอกสารเทคนิค, API, สเปคระบบ"
        ),
        "color": "#6366f1",  # indigo
    },
    {
        "name": "Research & Studies",
        "description": "Research papers and academic publications.",
        "keywords_text": (
            "research paper, journal article, scientific study, academic publication, "
            "dataset description, "
            "งานวิจัย, บทความวิชาการ, วารสาร"
        ),
        "color": "#14b8a6",  # teal
    },
    {
        "name": "Home & Household",
        "description": "Household management and property documents.",
        "keywords_text": (
            "utility bill, property document, maintenance record, warranty document, "
            "ค่าน้ำ, ค่าไฟ, โฉนด, ใบรับประกัน"
        ),
        "color": "#f97316",  # orange
    },
    {
        "name": "General Documents",
        "description": "Unclassified or mixed documents that don't fit a specific category.",
        "keywords_text": (
            "miscellaneous document, general file, uncategorized, "
            "เอกสารทั่วไป"
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
            keywords_text=defn["keywords_text"],
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

    Uses `name + description + keywords_text` as the embedding source
    text so the vector captures the full semantic breadth of the category.

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
        embed_text = _build_embed_text(cat)
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


def _build_embed_text(cat: Category) -> str:
    """
    Build the text blob used to generate a category's embedding.

    Combines name, description, and keywords_text for maximum
    semantic coverage.
    """
    parts = [cat.name]
    if cat.description:
        parts.append(cat.description)
    if cat.keywords_text:
        parts.append(cat.keywords_text)
    return ". ".join(parts)
