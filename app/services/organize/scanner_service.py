"""
Scanner Service — local file system metadata extraction.

Responsibilities:
  • Validate that file paths exist and are accessible
  • Extract metadata: size, extension, SHA-256 hash
  • Security: reject paths outside allowed roots / inside blocked dirs
  • NO file upload, NO file mutation
"""

import hashlib
import os
from pathlib import Path
from typing import Optional

import aiofiles

from app.core.config import settings
from app.observability.tracing import observe, update_current_span
from app.services.files.scan_result import ScanResult


class ScannerService:
    """Stateless service for scanning local files by absolute path."""

    # ── Public API ───────────────────────────────────────────────────────

    @observe(name="files.scan", capture_input=False, capture_output=False)
    async def scan(self, file_path: str) -> ScanResult:
        """
        Scan a single file and return its metadata.

        Raises nothing — errors are captured in `ScanResult.error`.
        """
        path = Path(file_path)
        update_current_span(input={"file_path": str(path)})

        # 1. Security checks
        violation = self._check_security(path)
        if violation:
            return self._error_result(file_path, violation)

        # 2. Existence & readability
        if not path.exists():
            return self._error_result(file_path, "File does not exist.")

        if not path.is_file():
            return self._error_result(file_path, "Path is not a regular file.")

        if not os.access(path, os.R_OK):
            return self._error_result(file_path, "Permission denied.")

        # 3. Metadata extraction
        try:
            stat = path.stat()
            sha256 = await self._hash_file(path)

            return ScanResult(
                original_path=str(path.resolve()),
                file_name=path.name,
                extension=path.suffix.lower(),
                size_bytes=stat.st_size,
                sha256=sha256,
                exists=True,
            )
        except Exception as exc:
            return self._error_result(file_path, f"Scan failed: {exc}")

    @observe(name="files.scan_many", capture_input=False, capture_output=False)
    async def scan_many(self, file_paths: list[str]) -> list[ScanResult]:
        """Scan multiple files sequentially (keeps I/O predictable)."""
        return [await self.scan(fp) for fp in file_paths]

    # ── Security ─────────────────────────────────────────────────────────

    def _check_security(self, path: Path) -> Optional[str]:
        """Return an error message if the path violates security rules."""
        try:
            resolved = path.resolve()
        except (OSError, ValueError):
            return "Invalid path."

        # Normalize to string for prefix checks
        resolved_str = str(resolved)

        # Block system directories
        for blocked in settings.blocked_directories:
            if resolved_str.startswith(blocked):
                return f"Access denied: path inside blocked directory ({blocked})."

        # Enforce allowed roots (if configured)
        if settings.allowed_roots:
            if not any(resolved_str.startswith(root) for root in settings.allowed_roots):
                return "Access denied: path outside allowed root directories."

        return None

    # ── Hashing ──────────────────────────────────────────────────────────

    @staticmethod
    async def _hash_file(path: Path, chunk_size: int = 8192) -> str:
        """Compute SHA-256 hash of a file asynchronously."""
        sha = hashlib.sha256()
        async with aiofiles.open(path, "rb") as f:
            while True:
                chunk = await f.read(chunk_size)
                if not chunk:
                    break
                sha.update(chunk)
        return sha.hexdigest()

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _error_result(file_path: str, error: str) -> ScanResult:
        return ScanResult(
            original_path=file_path,
            file_name=Path(file_path).name if file_path else "",
            extension="",
            size_bytes=0,
            sha256="",
            exists=False,
            error=error,
        )
