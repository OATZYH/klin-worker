"""Internal scanner result type for the service layer."""

from dataclasses import dataclass


@dataclass(slots=True)
class ScanResult:
    """Metadata extracted by `ScannerService` for a single file."""

    original_path: str
    file_name: str
    extension: str
    size_bytes: int
    sha256: str
    exists: bool
    error: str | None = None