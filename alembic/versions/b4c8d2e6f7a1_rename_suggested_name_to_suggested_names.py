"""rename_suggested_name_to_suggested_names

Revision ID: b4c8d2e6f7a1
Revises: a3b7c9d8e5f4
Create Date: 2026-03-07 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4c8d2e6f7a1'
down_revision: Union[str, Sequence[str], None] = 'a3b7c9d8e5f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Rename suggested_name → suggested_names and migrate existing data to JSON list."""
    with op.batch_alter_table('file_analysis', schema=None) as batch_op:
        batch_op.alter_column(
            'suggested_name',
            new_column_name='suggested_names',
            existing_type=sa.String(),
            existing_nullable=True,
        )

    # Wrap any existing single-string values in JSON array format
    op.execute(
        "UPDATE file_analysis "
        "SET suggested_names = '[\"' || suggested_names || '\"]' "
        "WHERE suggested_names IS NOT NULL "
        "AND suggested_names NOT LIKE '[%'"
    )


def downgrade() -> None:
    """Revert suggested_names → suggested_name, extracting first element."""
    # Extract first element from JSON array back to plain string
    op.execute(
        "UPDATE file_analysis "
        "SET suggested_names = json_extract(suggested_names, '$[0]') "
        "WHERE suggested_names IS NOT NULL "
        "AND suggested_names LIKE '[%'"
    )

    with op.batch_alter_table('file_analysis', schema=None) as batch_op:
        batch_op.alter_column(
            'suggested_names',
            new_column_name='suggested_name',
            existing_type=sa.String(),
            existing_nullable=True,
        )
