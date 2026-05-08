"""add_calendar_events

Revision ID: 9aa4fda488d6
Revises: c91b96bf166f
Create Date: 2026-05-08 18:58:51.798997

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


# revision identifiers, used by Alembic.
revision: str = '9aa4fda488d6'
down_revision: Union[str, Sequence[str], None] = 'c91b96bf166f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'detected_calendar_events',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('file_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('event_json', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('status', sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False),
        sa.Column('status_changed_at', sa.DateTime(), nullable=True),
        sa.Column('detected_at', sa.DateTime(), nullable=False),
        sa.Column('google_event_id', sqlmodel.sql.sqltypes.AutoString(length=128), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name='ck_detected_calendar_events_status_valid',
        ),
        sa.ForeignKeyConstraint(['file_id'], ['files.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_detected_calendar_events_file_id',
        'detected_calendar_events',
        ['file_id'],
        unique=True,
    )
    op.create_index(
        'ix_detected_calendar_events_status_detected_at',
        'detected_calendar_events',
        ['status', 'detected_at'],
        unique=False,
    )

    with op.batch_alter_table('file_analysis', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('calendar_event_json', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('file_analysis', schema=None) as batch_op:
        batch_op.drop_column('calendar_event_json')

    op.drop_index(
        'ix_detected_calendar_events_status_detected_at',
        table_name='detected_calendar_events',
    )
    op.drop_index(
        'ix_detected_calendar_events_file_id',
        table_name='detected_calendar_events',
    )
    op.drop_table('detected_calendar_events')
