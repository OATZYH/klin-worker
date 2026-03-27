"""add_watched_folders_and_onboarding_keys

Revision ID: d8f4b1a9c6e2
Revises: a7c3d1e9f4b2
Create Date: 2026-03-16 11:20:00.000000

"""
from datetime import datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


# revision identifiers, used by Alembic.
revision: str = "d8f4b1a9c6e2"
down_revision: Union[str, Sequence[str], None] = "a7c3d1e9f4b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_SETTING_DEFAULTS: dict[str, str | None] = {
    "onboarding_status": "pending",
    "onboarding_seeded": "false",
    "onboarding_started_at": None,
    "onboarding_seeded_at": None,
    "onboarding_completed_at": None,
    "seed_version": "1",
    "auto_organize_master_enabled": "false",
}


def _insert_setting_if_missing(bind: sa.Connection, key: str, value: str | None) -> None:
    existing = bind.execute(
        sa.text("SELECT 1 FROM app_settings WHERE key = :key LIMIT 1"),
        {"key": key},
    ).first()
    if existing is not None:
        return

    bind.execute(
        sa.text(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (:key, :value, :updated_at)
            """
        ),
        {
            "key": key,
            "value": value,
            "updated_at": datetime.utcnow(),
        },
    )


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "watched_folders",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("folder_path", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("auto_organize_enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("frequency_value", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "frequency_unit",
            sqlmodel.sql.sqltypes.AutoString(length=16),
            nullable=False,
            server_default=sa.text("'day'"),
        ),
        sa.Column("frequency_seconds", sa.Integer(), nullable=False, server_default=sa.text("86400")),
        sa.Column("recursive", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("last_scanned_at", sa.DateTime(), nullable=True),
        sa.Column("next_scan_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("frequency_value > 0", name="ck_watched_folders_frequency_value_positive"),
        sa.CheckConstraint("frequency_seconds > 0", name="ck_watched_folders_frequency_seconds_positive"),
        sa.CheckConstraint(
            "frequency_unit IN ('minute', 'hour', 'day')",
            name="ck_watched_folders_frequency_unit_valid",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("folder_path"),
    )

    op.create_index(
        "ix_watched_folders_enabled_next_scan",
        "watched_folders",
        ["auto_organize_enabled", "next_scan_at"],
        unique=False,
    )

    bind = op.get_bind()
    for key, value in _SETTING_DEFAULTS.items():
        _insert_setting_if_missing(bind, key, value)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_watched_folders_enabled_next_scan", table_name="watched_folders")
    op.drop_table("watched_folders")

    bind = op.get_bind()
    for key in _SETTING_DEFAULTS:
        bind.execute(sa.text("DELETE FROM app_settings WHERE key = :key"), {"key": key})
