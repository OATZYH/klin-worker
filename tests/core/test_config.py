"""Tests for app.core.config storage and version resolution.

Avoid reloading the config module — `settings` is captured by reference in
many service files at import time, so reloading creates two divergent
`settings` objects and breaks downstream tests via subtle state drift.
Instead, call the resolver helpers directly with monkeypatched environment.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.core import config

pytestmark = pytest.mark.unit


def test_resolve_default_storage_dir_dev_uses_project_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.delenv("KLIN_APP_DATA_DIR", raising=False)
    storage = config._resolve_default_storage_dir()
    assert storage.name == ".storage"


def test_resolve_default_storage_dir_frozen_uses_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("KLIN_APP_DATA_DIR", str(tmp_path / "klin"))
    storage = config._resolve_default_storage_dir()
    assert storage == Path(str(tmp_path / "klin"))


def test_resolve_default_storage_dir_frozen_falls_back_to_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delenv("KLIN_APP_DATA_DIR", raising=False)
    storage = config._resolve_default_storage_dir()
    assert storage == Path.home() / ".klin"


def test_resolve_app_version_prefers_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KLIN_APP_VERSION", "9.9.9")
    assert config._resolve_app_version() == "9.9.9"


def test_resolve_app_version_falls_back_to_version_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KLIN_APP_VERSION", raising=False)
    version = config._resolve_app_version()
    assert version
    assert version != "0.0.0"
