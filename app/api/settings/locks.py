"""
Lock settings router.

Stores AI-protection path locks in app_settings as two keys:
  - lock_file   : JSON string array of absolute file paths
  - lock_folder : JSON string array of absolute folder paths

Mounted at: /api/settings/locks
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import AppSetting
from app.db.session import get_db
from app.models.request import LockSettingsUpdateRequest
from app.models.response import LockSettingStatus, LockSettingsResponse

router = APIRouter(tags=["settings"])

SETTING_KEY_LOCK_FILE = "lock_file"
SETTING_KEY_LOCK_FOLDER = "lock_folder"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_paths(paths: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()

    for raw in paths:
        value = str(raw).strip()
        if not value:
            continue

        key = value.casefold()
        if key in seen:
            continue

        seen.add(key)
        normalized.append(value)

    return normalized


def _decode_paths(setting: AppSetting | None) -> list[str]:
    if not setting or not setting.value:
        return []

    try:
        data = json.loads(setting.value)
    except json.JSONDecodeError:
        return []

    if not isinstance(data, list):
        return []

    return _normalize_paths([str(item) for item in data])


async def _upsert_setting(db: AsyncSession, *, key: str, value: list[str]) -> AppSetting:
    encoded = json.dumps(_normalize_paths(value))
    setting = await db.get(AppSetting, key)
    if setting:
        setting.value = encoded
        setting.updated_at = _utcnow()
    else:
        setting = AppSetting(key=key, value=encoded, updated_at=_utcnow())
        db.add(setting)

    await db.flush()
    return setting


@router.get("/locks", response_model=LockSettingsResponse)
async def get_locks(db: AsyncSession = Depends(get_db)) -> LockSettingsResponse:
    lock_file_setting = await db.get(AppSetting, SETTING_KEY_LOCK_FILE)
    lock_folder_setting = await db.get(AppSetting, SETTING_KEY_LOCK_FOLDER)

    return LockSettingsResponse(
        lock_file=_decode_paths(lock_file_setting),
        lock_folder=_decode_paths(lock_folder_setting),
        lock_file_status=LockSettingStatus(
            key=SETTING_KEY_LOCK_FILE,
            updated_at=lock_file_setting.updated_at if lock_file_setting else None,
        ),
        lock_folder_status=LockSettingStatus(
            key=SETTING_KEY_LOCK_FOLDER,
            updated_at=lock_folder_setting.updated_at if lock_folder_setting else None,
        ),
    )


@router.put("/locks", response_model=LockSettingsResponse)
async def put_locks(body: LockSettingsUpdateRequest, db: AsyncSession = Depends(get_db)) -> LockSettingsResponse:
    lock_file_setting = await _upsert_setting(db, key=SETTING_KEY_LOCK_FILE, value=body.lock_file)
    lock_folder_setting = await _upsert_setting(db, key=SETTING_KEY_LOCK_FOLDER, value=body.lock_folder)

    return LockSettingsResponse(
        lock_file=_decode_paths(lock_file_setting),
        lock_folder=_decode_paths(lock_folder_setting),
        lock_file_status=LockSettingStatus(
            key=SETTING_KEY_LOCK_FILE,
            updated_at=lock_file_setting.updated_at,
        ),
        lock_folder_status=LockSettingStatus(
            key=SETTING_KEY_LOCK_FOLDER,
            updated_at=lock_folder_setting.updated_at,
        ),
    )
