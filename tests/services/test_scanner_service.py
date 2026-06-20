"""Tests for ScannerService — file metadata extraction and security checks."""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import pytest

from app.services.organize.scanner_service import ScannerService

pytestmark = pytest.mark.unit


# ── _hash_file ───────────────────────────────────────────────────────────


async def test_hash_file_matches_sha256(tmp_path: Path) -> None:
    file_path = tmp_path / "blob.bin"
    payload = b"the quick brown fox jumps over the lazy dog"
    file_path.write_bytes(payload)

    digest = await ScannerService._hash_file(file_path)
    assert digest == hashlib.sha256(payload).hexdigest()


# ── _check_security ──────────────────────────────────────────────────────


def test_check_security_blocks_system_directories(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.core import config as config_module

    monkeypatch.setattr(
        config_module.settings,
        "blocked_directories",
        [str(tmp_path)],
        raising=False,
    )
    monkeypatch.setattr(config_module.settings, "allowed_roots", [], raising=False)
    svc = ScannerService()

    target = tmp_path / "x.txt"
    target.write_text("hi")
    err = svc._check_security(target)
    assert err is not None
    assert "blocked directory" in err


def test_check_security_enforces_allowed_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.core import config as config_module

    monkeypatch.setattr(config_module.settings, "blocked_directories", [], raising=False)
    monkeypatch.setattr(
        config_module.settings,
        "allowed_roots",
        ["/some/nonexistent/root"],
        raising=False,
    )
    svc = ScannerService()

    target = tmp_path / "x.txt"
    target.write_text("hi")
    err = svc._check_security(target)
    assert err is not None
    assert "allowed root" in err


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX symlink semantics only")
def test_check_security_blocks_symlink_pointing_outside_allowed_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.core import config as config_module

    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()

    secret = outside / "secret.txt"
    secret.write_text("classified")
    link = allowed / "link.txt"
    link.symlink_to(secret)

    monkeypatch.setattr(config_module.settings, "blocked_directories", [], raising=False)
    monkeypatch.setattr(
        config_module.settings,
        "allowed_roots",
        [str(allowed.resolve())],
        raising=False,
    )

    svc = ScannerService()
    err = svc._check_security(link)
    assert err is not None
    assert "allowed root" in err


def test_check_security_passes_for_unrestricted_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.core import config as config_module

    monkeypatch.setattr(config_module.settings, "blocked_directories", [], raising=False)
    monkeypatch.setattr(config_module.settings, "allowed_roots", [], raising=False)
    svc = ScannerService()

    target = tmp_path / "ok.txt"
    target.write_text("hi")
    assert svc._check_security(target) is None


# ── scan ────────────────────────────────────────────────────────────────


async def test_scan_returns_metadata_for_existing_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.core import config as config_module

    monkeypatch.setattr(config_module.settings, "blocked_directories", [], raising=False)
    monkeypatch.setattr(config_module.settings, "allowed_roots", [], raising=False)

    target = tmp_path / "doc.pdf"
    payload = b"hello world"
    target.write_bytes(payload)

    svc = ScannerService()
    result = await svc.scan(str(target))

    assert result.exists is True
    assert result.error is None
    assert result.file_name == "doc.pdf"
    assert result.extension == ".pdf"
    assert result.size_bytes == len(payload)
    assert result.sha256 == hashlib.sha256(payload).hexdigest()


async def test_scan_returns_error_for_missing_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.core import config as config_module

    monkeypatch.setattr(config_module.settings, "blocked_directories", [], raising=False)
    monkeypatch.setattr(config_module.settings, "allowed_roots", [], raising=False)

    svc = ScannerService()
    result = await svc.scan(str(tmp_path / "missing.pdf"))
    assert result.exists is False
    assert result.error == "File does not exist."


async def test_scan_rejects_directory_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.core import config as config_module

    monkeypatch.setattr(config_module.settings, "blocked_directories", [], raising=False)
    monkeypatch.setattr(config_module.settings, "allowed_roots", [], raising=False)

    svc = ScannerService()
    result = await svc.scan(str(tmp_path))
    assert result.exists is False
    assert result.error == "Path is not a regular file."


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX permission semantics only")
async def test_scan_reports_permission_denied(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.core import config as config_module

    monkeypatch.setattr(config_module.settings, "blocked_directories", [], raising=False)
    monkeypatch.setattr(config_module.settings, "allowed_roots", [], raising=False)

    target = tmp_path / "locked.txt"
    target.write_text("secret")
    os.chmod(target, 0)
    try:
        # Skip when running as root (chmod 0 is bypassed).
        if os.geteuid() == 0:
            pytest.skip("Cannot simulate permission denied as root")
        svc = ScannerService()
        result = await svc.scan(str(target))
        assert result.exists is False
        assert result.error == "Permission denied."
    finally:
        os.chmod(target, 0o644)
