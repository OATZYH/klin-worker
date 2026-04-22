"""Shared lock settings and lock matching service."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Iterable

from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import AppSetting
from app.observability.tracing import observe

SETTING_KEY_LOCK_FILE = "lock_file"
SETTING_KEY_LOCK_FOLDER = "lock_folder"


@dataclass(frozen=True)
class LockSettingsSnapshot:
    lock_file: list[str]
    lock_folder: list[str]
    lock_file_updated_at: datetime | None
    lock_folder_updated_at: datetime | None


@dataclass(frozen=True)
class LockedPath:
    path: str
    reason: str


class LockSettingsService:
    """Central source of truth for lock setting persistence and matching."""

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def normalize_path_for_match(value: str) -> str:
        return value.strip().replace("\\", "/").rstrip("/").lower()

    @classmethod
    def normalize_paths(cls, paths: Iterable[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()

        for raw in paths:
            value = str(raw).strip()
            if not value:
                continue

            key = cls.normalize_path_for_match(value)
            if key in seen:
                continue

            seen.add(key)
            normalized.append(value)

        return normalized

    @staticmethod
    def is_absolute_path(value: str) -> bool:
        path = str(value).strip()
        if not path:
            return False
        if Path(path).expanduser().is_absolute():
            return True
        return PureWindowsPath(path).is_absolute()

    @classmethod
    def validate_absolute_paths(cls, *, paths: Iterable[str], field_name: str) -> None:
        invalid = [str(path).strip() for path in paths if not cls.is_absolute_path(str(path))]
        if not invalid:
            return

        sample = ", ".join(repr(path) for path in invalid[:3])
        suffix = "" if len(invalid) <= 3 else f" (+{len(invalid) - 3} more)"
        raise ValueError(
            f"{field_name} must contain only absolute paths. Invalid: {sample}{suffix}"
        )

    @classmethod
    def decode_paths(cls, value: str | None) -> list[str]:
        if not value:
            return []

        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            return []

        if not isinstance(payload, list):
            return []

        return cls.normalize_paths([str(item) for item in payload])

    @classmethod
    def get_lock_reason(cls, path: str, settings: LockSettingsSnapshot) -> str | None:
        normalized = cls.normalize_path_for_match(path)
        if not normalized:
            return None

        for locked_file in settings.lock_file:
            if cls.normalize_path_for_match(locked_file) == normalized:
                return f"file is directly locked: {locked_file}"

        for locked_folder in settings.lock_folder:
            normalized_folder = cls.normalize_path_for_match(locked_folder)
            if normalized == normalized_folder or normalized.startswith(f"{normalized_folder}/"):
                return f"file is locked by folder: {locked_folder}"

        return None

    @classmethod
    def partition_paths(
        cls,
        *,
        file_paths: Iterable[str],
        settings: LockSettingsSnapshot,
    ) -> tuple[list[str], list[LockedPath]]:
        unlocked: list[str] = []
        locked: list[LockedPath] = []

        for path in file_paths:
            reason = cls.get_lock_reason(path, settings)
            if reason:
                locked.append(LockedPath(path=path, reason=reason))
                continue
            unlocked.append(path)

        return unlocked, locked

    @observe(name="locks.get_settings", capture_input=False, capture_output=False)
    async def get_settings(self, db: AsyncSession) -> LockSettingsSnapshot:
        lock_file_setting = await db.get(AppSetting, SETTING_KEY_LOCK_FILE)
        lock_folder_setting = await db.get(AppSetting, SETTING_KEY_LOCK_FOLDER)

        return LockSettingsSnapshot(
            lock_file=self.decode_paths(lock_file_setting.value if lock_file_setting else None),
            lock_folder=self.decode_paths(
                lock_folder_setting.value if lock_folder_setting else None
            ),
            lock_file_updated_at=lock_file_setting.updated_at if lock_file_setting else None,
            lock_folder_updated_at=lock_folder_setting.updated_at if lock_folder_setting else None,
        )

    @observe(name="locks.update_settings", capture_input=False, capture_output=False)
    async def update_settings(
        self,
        db: AsyncSession,
        *,
        lock_file: list[str],
        lock_folder: list[str],
    ) -> LockSettingsSnapshot:
        normalized_lock_file = self.normalize_paths(lock_file)
        normalized_lock_folder = self.normalize_paths(lock_folder)

        lock_file_setting = await self._upsert_setting(
            db,
            key=SETTING_KEY_LOCK_FILE,
            value=normalized_lock_file,
        )
        lock_folder_setting = await self._upsert_setting(
            db,
            key=SETTING_KEY_LOCK_FOLDER,
            value=normalized_lock_folder,
        )

        return LockSettingsSnapshot(
            lock_file=normalized_lock_file,
            lock_folder=normalized_lock_folder,
            lock_file_updated_at=lock_file_setting.updated_at,
            lock_folder_updated_at=lock_folder_setting.updated_at,
        )

    async def _upsert_setting(
        self,
        db: AsyncSession,
        *,
        key: str,
        value: list[str],
    ) -> AppSetting:
        encoded = json.dumps(value)
        setting = await db.get(AppSetting, key)
        if setting:
            setting.value = encoded
            setting.updated_at = self._utcnow()
        else:
            setting = AppSetting(key=key, value=encoded, updated_at=self._utcnow())
            db.add(setting)

        await db.flush()
        return setting
