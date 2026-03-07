"""add_system_logs_table

Revision ID: f2a1b6c4d8e9
Revises: e1f7c3b9a2d4
Create Date: 2026-03-07 18:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


# revision identifiers, used by Alembic.
revision: str = "f2a1b6c4d8e9"
down_revision: Union[str, Sequence[str], None] = "e1f7c3b9a2d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "system_logs",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("level", sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False),
        sa.Column("component", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("event_type", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
        sa.Column("message", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("context_json", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("correlation_id", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_system_logs_created_at", "system_logs", ["created_at"], unique=False)
    op.create_index("ix_system_logs_component", "system_logs", ["component"], unique=False)
    op.create_index("ix_system_logs_level", "system_logs", ["level"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_system_logs_level", table_name="system_logs")
    op.drop_index("ix_system_logs_component", table_name="system_logs")
    op.drop_index("ix_system_logs_created_at", table_name="system_logs")
    op.drop_table("system_logs")
