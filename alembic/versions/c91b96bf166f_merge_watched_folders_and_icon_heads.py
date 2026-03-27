"""merge_watched_folders_and_icon_heads

Revision ID: c91b96bf166f
Revises: d8f4b1a9c6e2, e6a9c1d2f3b4
Create Date: 2026-03-27 17:19:58.870892

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


# revision identifiers, used by Alembic.
revision: str = 'c91b96bf166f'
down_revision: Union[str, Sequence[str], None] = ('d8f4b1a9c6e2', 'e6a9c1d2f3b4')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
