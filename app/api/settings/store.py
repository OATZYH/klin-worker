"""Helpers for app_settings key-value persistence and parsing."""

from datetime import datetime

from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import AppSetting

SETTING_KEY_ONBOARDING_STATUS = "onboarding_status"
SETTING_KEY_ONBOARDING_STARTED_AT = "onboarding_started_at"
SETTING_KEY_ONBOARDING_SEEDED_AT = "onboarding_seeded_at"
SETTING_KEY_ONBOARDING_COMPLETED_AT = "onboarding_completed_at"
SETTING_KEY_SEED_VERSION = "seed_version"
SETTING_KEY_AUTO_ORGANIZE_MASTER_ENABLED = "auto_organize_master_enabled"


async def get_setting_value(
    db: AsyncSession,
    key: str,
    default: str | None = None,
) -> str | None:
    setting = await db.get(AppSetting, key)
    if setting is None:
        return default
    return setting.value


async def upsert_setting_value(
    db: AsyncSession,
    key: str,
    value: str | None,
) -> None:
    setting = await db.get(AppSetting, key)
    if setting is None:
        db.add(AppSetting(key=key, value=value))
        return
    setting.value = value


def parse_bool_setting(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_datetime_setting(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
