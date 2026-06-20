from __future__ import annotations

from pathlib import Path
import tomllib

from app.core.config import _resolve_app_version


def test_version_file_matches_pyproject() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    pyproject_data = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    pyproject_version = str(pyproject_data["project"]["version"]).strip()
    version_file = (repo_root / "VERSION").read_text(encoding="utf-8").strip()

    assert version_file
    assert pyproject_version == version_file


def test_resolve_app_version_prefers_env(monkeypatch) -> None:
    monkeypatch.setenv("KLIN_APP_VERSION", "9.9.9")
    assert _resolve_app_version() == "9.9.9"
