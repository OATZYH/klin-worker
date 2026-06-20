"""add categories_hash to file_analysis

Revision ID: c9d3e4f5a6b2
Revises: b4c8d2e6f7a1
Create Date: 2026-03-07 20:00:00.000000

Adds a `categories_hash` column to `file_analysis` that stores an MD5
fingerprint of the active categories at the time of analysis.  Used by
the organize pipeline to detect when categories have changed and a
partial re-classification (without a full LLM re-run) is needed.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c9d3e4f5a6b2"
down_revision: Union[str, Sequence[str], None] = "b4c8d2e6f7a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("file_analysis", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("categories_hash", sa.String(), nullable=True)
        )
    # Existing rows get NULL — this intentionally causes a one-time
    # re-classification on next request so stale cached scores are refreshed.


def downgrade() -> None:
    with op.batch_alter_table("file_analysis", schema=None) as batch_op:
        batch_op.drop_column("categories_hash")
