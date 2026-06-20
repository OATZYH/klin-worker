"""add_current_path_to_files

Revision ID: a7c3d1e9f4b2
Revises: f2a1b6c4d8e9
Create Date: 2026-03-07 20:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


# revision identifiers, used by Alembic.
revision: str = "a7c3d1e9f4b2"
down_revision: Union[str, Sequence[str], None] = "f2a1b6c4d8e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("files") as batch_op:
        batch_op.add_column(sa.Column("current_path", sqlmodel.sql.sqltypes.AutoString(), nullable=True))

    op.execute("UPDATE files SET current_path = original_path WHERE current_path IS NULL")

    with op.batch_alter_table("files") as batch_op:
        batch_op.alter_column("current_path", existing_type=sqlmodel.sql.sqltypes.AutoString(), nullable=False)
        batch_op.create_index("ix_files_current_path", ["current_path"], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("files") as batch_op:
        batch_op.drop_index("ix_files_current_path")
        batch_op.drop_column("current_path")
