"""Auto-organize watcher settings routes.

Mounted under /api/settings/auto-organize.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.settings.store import (
    SETTING_KEY_AUTO_ORGANIZE_MASTER_ENABLED,
    get_setting_value,
    parse_bool_setting,
    upsert_setting_value,
)
from app.db.models import WatchedFolder
from app.db.session import get_db
from app.models.request import (
    AutoOrganizeSettingsUpdate,
    WatcherFolderCreate,
    WatcherFolderUpdate,
)
from app.models.response import (
    AutoOrganizeSettingsResponse,
    WatcherFolderResponse,
    WatcherFoldersResponse,
)

router = APIRouter(prefix="/auto-organize", tags=["settings"])

_FREQUENCY_SECONDS_BY_UNIT: dict[str, int] = {
    "minute": 60,
    "hour": 3600,
    "day": 86400,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_folder_path(folder_path: str) -> str:
    p = Path(folder_path).expanduser()
    if not p.is_absolute():
        raise HTTPException(status_code=400, detail="folder_path must be absolute.")
    if not p.exists():
        raise HTTPException(status_code=400, detail=f"Folder does not exist: {p}")
    if not p.is_dir():
        raise HTTPException(status_code=400, detail=f"Path is not a directory: {p}")
    return str(p.resolve())


def _frequency_seconds(value: int, unit: str) -> int:
    return value * _FREQUENCY_SECONDS_BY_UNIT[unit]


def _to_folder_response(folder: WatchedFolder) -> WatcherFolderResponse:
    return WatcherFolderResponse(
        id=folder.id,
        folder_path=folder.folder_path,
        auto_organize_enabled=folder.auto_organize_enabled,
        frequency_value=folder.frequency_value,
        frequency_unit=folder.frequency_unit,
        frequency_seconds=folder.frequency_seconds,
        recursive=folder.recursive,
        last_scanned_at=folder.last_scanned_at,
        next_scan_at=folder.next_scan_at,
        last_error=folder.last_error,
        created_at=folder.created_at,
        updated_at=folder.updated_at,
    )


@router.get("", response_model=AutoOrganizeSettingsResponse)
async def get_auto_organize_settings(
    db: AsyncSession = Depends(get_db),
) -> AutoOrganizeSettingsResponse:
    enabled = parse_bool_setting(
        await get_setting_value(db, SETTING_KEY_AUTO_ORGANIZE_MASTER_ENABLED),
        default=False,
    )
    return AutoOrganizeSettingsResponse(enabled=enabled)


@router.put("", response_model=AutoOrganizeSettingsResponse)
async def set_auto_organize_settings(
    body: AutoOrganizeSettingsUpdate,
    db: AsyncSession = Depends(get_db),
) -> AutoOrganizeSettingsResponse:
    await upsert_setting_value(
        db,
        SETTING_KEY_AUTO_ORGANIZE_MASTER_ENABLED,
        "true" if body.enabled else "false",
    )
    await db.flush()
    return AutoOrganizeSettingsResponse(enabled=body.enabled)


@router.get("/folders", response_model=WatcherFoldersResponse)
async def list_watcher_folders(
    db: AsyncSession = Depends(get_db),
) -> WatcherFoldersResponse:
    result = await db.execute(
        select(WatchedFolder).order_by(WatchedFolder.created_at)  # type: ignore[union-attr]
    )
    folders = result.scalars().all()
    return WatcherFoldersResponse(results=[_to_folder_response(f) for f in folders])


@router.post("/folders", response_model=WatcherFolderResponse, status_code=201)
async def create_watcher_folder(
    body: WatcherFolderCreate,
    db: AsyncSession = Depends(get_db),
) -> WatcherFolderResponse:
    normalized_path = _normalize_folder_path(body.folder_path)

    existing = await db.execute(
        select(WatchedFolder).where(WatchedFolder.folder_path == normalized_path)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Watcher folder already exists.")

    now = _utcnow()
    frequency_seconds = _frequency_seconds(body.frequency_value, body.frequency_unit)

    folder = WatchedFolder(
        folder_path=normalized_path,
        auto_organize_enabled=body.auto_organize_enabled,
        frequency_value=body.frequency_value,
        frequency_unit=body.frequency_unit,
        frequency_seconds=frequency_seconds,
        recursive=body.recursive,
        next_scan_at=(now + timedelta(seconds=frequency_seconds))
        if body.auto_organize_enabled
        else None,
        created_at=now,
        updated_at=now,
    )
    db.add(folder)
    await db.flush()

    return _to_folder_response(folder)


@router.patch("/folders/{watcher_id}", response_model=WatcherFolderResponse)
async def update_watcher_folder(
    watcher_id: str,
    body: WatcherFolderUpdate,
    db: AsyncSession = Depends(get_db),
) -> WatcherFolderResponse:
    folder = await db.get(WatchedFolder, watcher_id)
    if folder is None:
        raise HTTPException(status_code=404, detail="Watcher folder not found.")

    if body.folder_path is not None:
        normalized_path = _normalize_folder_path(body.folder_path)
        if normalized_path != folder.folder_path:
            duplicate = await db.execute(
                select(WatchedFolder).where(
                    WatchedFolder.folder_path == normalized_path,
                    WatchedFolder.id != watcher_id,
                )
            )
            if duplicate.scalar_one_or_none() is not None:
                raise HTTPException(status_code=409, detail="Watcher folder already exists.")
            folder.folder_path = normalized_path

    if body.auto_organize_enabled is not None:
        folder.auto_organize_enabled = body.auto_organize_enabled
    if body.frequency_value is not None:
        folder.frequency_value = body.frequency_value
    if body.frequency_unit is not None:
        folder.frequency_unit = body.frequency_unit
    if body.recursive is not None:
        folder.recursive = body.recursive

    now = _utcnow()
    folder.frequency_seconds = _frequency_seconds(folder.frequency_value, folder.frequency_unit)
    folder.next_scan_at = (
        now + timedelta(seconds=folder.frequency_seconds)
        if folder.auto_organize_enabled
        else None
    )
    folder.updated_at = now

    await db.flush()
    return _to_folder_response(folder)


@router.delete("/folders/{watcher_id}", status_code=204)
async def delete_watcher_folder(
    watcher_id: str,
    db: AsyncSession = Depends(get_db),
) -> Response:
    folder = await db.get(WatchedFolder, watcher_id)
    if folder is None:
        raise HTTPException(status_code=404, detail="Watcher folder not found.")
    await db.delete(folder)
    return Response(status_code=204)


