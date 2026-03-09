"""Shared AI capability errors and helpers."""

from collections.abc import Iterable

from fastapi import HTTPException, status


class AiCapabilityUnavailableError(RuntimeError):
    """Raised when a required AI capability is unavailable."""

    def __init__(self, capability: str, detail: str) -> None:
        self.capability = capability
        self.detail = detail
        super().__init__(detail)


def to_service_unavailable_http_exception(
    exc: AiCapabilityUnavailableError,
) -> HTTPException:
    """Convert an AI capability failure into a FastAPI 503 response."""
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=exc.detail,
    )


def format_ai_capability_errors(
    errors: Iterable[AiCapabilityUnavailableError],
) -> str:
    """Merge multiple AI capability errors into a single user-facing message."""
    unique_errors: list[AiCapabilityUnavailableError] = []
    seen_pairs: set[tuple[str, str]] = set()
    seen_capabilities: set[str] = set()
    capabilities: list[str] = []

    for error in errors:
        key = (error.capability, error.detail)
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        unique_errors.append(error)
        if error.capability not in seen_capabilities:
            seen_capabilities.add(error.capability)
            capabilities.append(error.capability)

    if not unique_errors:
        return "Required AI capabilities are unavailable."

    capability_text = ", ".join(capabilities)
    detail_text = " ".join(error.detail for error in unique_errors)
    return (
        f"Required AI capabilities unavailable: {capability_text}. "
        f"{detail_text}"
    )