"""add_app_settings_and_is_path_manual

Revision ID: a3b7c9d8e5f4
Revises: 07374a4c372c
Create Date: 2026-03-03 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


# revision identifiers, used by Alembic.
revision: str = 'a3b7c9d8e5f4'
down_revision: Union[str, Sequence[str], None] = '07374a4c372c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Create app_settings table
    op.create_table('app_settings',
        sa.Column('key', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('value', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('key')
    )

    # Add is_path_manual column to categories
    with op.batch_alter_table('categories', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_path_manual', sa.Boolean(), nullable=False, server_default=sa.text('0')))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('categories', schema=None) as batch_op:
        batch_op.drop_column('is_path_manual')

    op.drop_table('app_settings')
