"""add_icon_to_categories

Revision ID: e6a9c1d2f3b4
Revises: d5f6a7b8c9e1
Create Date: 2026-03-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e6a9c1d2f3b4"
down_revision: Union[str, Sequence[str], None] = "d5f6a7b8c9e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("categories", schema=None) as batch_op:
        batch_op.add_column(sa.Column("icon", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("categories", schema=None) as batch_op:
        batch_op.drop_column("icon")
