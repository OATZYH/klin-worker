"""
Lock settings router.

Stores AI-protection path locks in app_settings as two keys:
  - lock_file   : JSON string array of absolute file paths
  - lock_folder : JSON string array of absolute folder paths

Mounted at: /api/settings/locks
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.session import get_db
from app.models.request import LockSettingsUpdateRequest
from app.models.response import LockSettingStatus, LockSettingsResponse
from app.services.lock_settings_service import (
    LockSettingsService,
    SETTING_KEY_LOCK_FILE,
    SETTING_KEY_LOCK_FOLDER,
)

router = APIRouter(tags=["settings"])


def _get_lock_settings_service() -> LockSettingsService:
    return LockSettingsService()


@router.get("/locks", response_model=LockSettingsResponse)
async def get_locks(
    db: AsyncSession = Depends(get_db),
    lock_svc: LockSettingsService = Depends(_get_lock_settings_service),
) -> LockSettingsResponse:
    settings = await lock_svc.get_settings(db)

    return LockSettingsResponse(
        lock_file=settings.lock_file,
        lock_folder=settings.lock_folder,
        lock_file_status=LockSettingStatus(
            key=SETTING_KEY_LOCK_FILE,
            updated_at=settings.lock_file_updated_at,
        ),
        lock_folder_status=LockSettingStatus(
            key=SETTING_KEY_LOCK_FOLDER,
            updated_at=settings.lock_folder_updated_at,
        ),
    )


@router.put("/locks", response_model=LockSettingsResponse)
async def put_locks(
    body: LockSettingsUpdateRequest,
    db: AsyncSession = Depends(get_db),
    lock_svc: LockSettingsService = Depends(_get_lock_settings_service),
) -> LockSettingsResponse:
    try:
        lock_svc.validate_absolute_paths(paths=body.lock_file, field_name="lock_file")
        lock_svc.validate_absolute_paths(paths=body.lock_folder, field_name="lock_folder")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    settings = await lock_svc.update_settings(
        db,
        lock_file=body.lock_file,
        lock_folder=body.lock_folder,
    )

    return LockSettingsResponse(
        lock_file=settings.lock_file,
        lock_folder=settings.lock_folder,
        lock_file_status=LockSettingStatus(
            key=SETTING_KEY_LOCK_FILE,
            updated_at=settings.lock_file_updated_at,
        ),
        lock_folder_status=LockSettingStatus(
            key=SETTING_KEY_LOCK_FOLDER,
            updated_at=settings.lock_folder_updated_at,
        ),
    )
