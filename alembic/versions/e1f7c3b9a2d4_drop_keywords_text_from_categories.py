"""drop keywords_text from categories

Revision ID: e1f7c3b9a2d4
Revises: c9d3e4f5a6b2
Create Date: 2026-03-07 23:10:00.000000

Backfills legacy category keyword text into `description`, invalidates
cached category classifications, and removes the obsolete column.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e1f7c3b9a2d4"
down_revision: Union[str, Sequence[str], None] = "c9d3e4f5a6b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()

    bind.execute(
        sa.text(
            """
            UPDATE categories
            SET description = CASE
                WHEN keywords_text IS NULL OR trim(keywords_text) = '' THEN COALESCE(description, '')
                WHEN description IS NULL OR trim(description) = '' THEN trim(keywords_text)
                WHEN instr(description, trim(keywords_text)) > 0 THEN trim(description)
                ELSE rtrim(description) || char(10) || trim(keywords_text)
            END
            """
        )
    )
    bind.execute(sa.text("UPDATE categories SET embedding = NULL"))
    bind.execute(sa.text("UPDATE file_analysis SET categories_hash = NULL"))

    with op.batch_alter_table("categories", schema=None) as batch_op:
        batch_op.drop_column("keywords_text")


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("categories", schema=None) as batch_op:
        batch_op.add_column(sa.Column("keywords_text", sa.String(), nullable=True))
