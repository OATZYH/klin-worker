"""add_is_auto_description_to_categories

Revision ID: d5f6a7b8c9e1
Revises: a7c3d1e9f4b2
Create Date: 2026-03-08 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d5f6a7b8c9e1"
down_revision: Union[str, Sequence[str], None] = "a7c3d1e9f4b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add is_auto_description flag to categories.

    True when the description was auto-generated from a folder path (batch import).
    Cleared to False when the user manually edits the description via PATCH.
    """
    with op.batch_alter_table("categories", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_auto_description",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )


def downgrade() -> None:
    """Remove is_auto_description flag from categories."""
    with op.batch_alter_table("categories", schema=None) as batch_op:
        batch_op.drop_column("is_auto_description")
