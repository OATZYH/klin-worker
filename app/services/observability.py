"""
Langfuse observability helpers.

This module centralizes Langfuse client initialization, masking policy,
request/trace context propagation, and safe helpers that never break the
application when tracing backends are unavailable.
"""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any, Iterator, Optional

from langfuse import Langfuse

from app.core.config import settings

logger = logging.getLogger(__name__)

# Request-scoped observability context
_request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
_trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)

# Lazy singleton state
_langfuse_client: Langfuse | None = None
_langfuse_init_attempted: bool = False
_langfuse_init_error: str | None = None

_PATH_PATTERN = re.compile(r"([A-Za-z]:\\\\|/)[^\n\r\t ]+")


class NoopObservation:
    """Small no-op observation object used when tracing is disabled/unavailable."""

    trace_id: str | None = None
    id: str | None = None

    def update(self, **_: Any) -> "NoopObservation":
        return self

    def end(self, **_: Any) -> "NoopObservation":
        return self


NOOP_OBSERVATION = NoopObservation()


def _capture_full_payloads() -> bool:
    """Allow full prompt/response payloads only when running in debug mode."""
    return settings.debug and settings.langfuse_capture_full_io_in_debug


def _mask_data_recursive(data: Any, *, key: str | None = None) -> Any:
    """Mask/truncate payloads before they are sent to Langfuse."""
    if _capture_full_payloads():
        return data

    if isinstance(data, dict):
        return {k: _mask_data_recursive(v, key=k) for k, v in data.items()}

    if isinstance(data, list):
        return [_mask_data_recursive(item, key=key) for item in data]

    if isinstance(data, tuple):
        return tuple(_mask_data_recursive(item, key=key) for item in data)

    if isinstance(data, str):
        text = data

        if settings.langfuse_mask_file_paths and key and "path" in key.lower():
            return "[masked-path]"

        if settings.langfuse_mask_file_paths:
            text = _PATH_PATTERN.sub("[masked-path]", text)

        max_chars = max(32, settings.langfuse_max_text_capture_chars)
        if len(text) > max_chars:
            extra = len(text) - max_chars
            return f"{text[:max_chars]}... [truncated {extra} chars]"

        return text

    return data


def _mask_data(*, data: Any, **kwargs: dict[str, Any]) -> Any:
    """Adapter that matches Langfuse mask callback type annotations."""
    key_value = kwargs.get("key")
    key = key_value if isinstance(key_value, str) else None
    return _mask_data_recursive(data, key=key)


def _build_langfuse_client() -> Langfuse:
    """Initialize Langfuse with configured batching and masking policies."""
    return Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
        sample_rate=settings.langfuse_sample_rate,
        flush_at=settings.langfuse_flush_at,
        flush_interval=settings.langfuse_flush_interval_seconds,
        mask=_mask_data,
    )


def is_langfuse_enabled() -> bool:
    """Return whether Langfuse tracing is enabled by configuration."""
    return settings.langfuse_enabled


def get_langfuse_init_error() -> str | None:
    """Return initialization error for diagnostics/health checks."""
    return _langfuse_init_error


def get_langfuse_client() -> Optional[Langfuse]:
    """Return an initialized Langfuse client, or None when unavailable."""
    global _langfuse_client, _langfuse_init_attempted, _langfuse_init_error

    if not settings.langfuse_enabled:
        return None

    if _langfuse_client is not None:
        return _langfuse_client

    if _langfuse_init_attempted:
        return None

    _langfuse_init_attempted = True

    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        _langfuse_init_error = (
            "Langfuse enabled but credentials are missing "
            "(KLIN_LANGFUSE_PUBLIC_KEY / KLIN_LANGFUSE_SECRET_KEY)."
        )
        logger.warning(_langfuse_init_error)
        return None

    try:
        _langfuse_client = _build_langfuse_client()
        _langfuse_init_error = None
        logger.info("Langfuse tracing enabled (host=%s)", settings.langfuse_host)
    except Exception as exc:
        _langfuse_init_error = str(exc)
        _langfuse_client = None
        logger.warning("Failed to initialize Langfuse client: %s", exc)

    return _langfuse_client


def create_trace_id(seed: str) -> str | None:
    """Create deterministic trace ids for request correlation when possible."""
    client = get_langfuse_client()
    if client is None:
        return None

    try:
        return client.create_trace_id(seed=seed)
    except Exception as exc:
        logger.debug("Unable to create Langfuse trace id from seed '%s': %s", seed, exc)
        return None


def set_request_trace_context(
    *,
    request_id: str,
    trace_id: str | None,
) -> tuple[Token[str | None], Token[str | None]]:
    """Store request/trace ids in ContextVars and return reset tokens."""
    return _request_id_var.set(request_id), _trace_id_var.set(trace_id)


def reset_request_trace_context(tokens: tuple[Token[str | None], Token[str | None]]) -> None:
    """Reset request/trace context variables from previously returned tokens."""
    request_token, trace_token = tokens
    _request_id_var.reset(request_token)
    _trace_id_var.reset(trace_token)


def get_current_request_id() -> str | None:
    """Return the active request id if running inside a request scope."""
    return _request_id_var.get()


def get_current_trace_id() -> str | None:
    """Return the active Langfuse trace id if present in context."""
    return _trace_id_var.get()


@contextmanager
def safe_start_observation(
    *,
    name: str,
    as_type: str = "span",
    trace_context: dict[str, str] | None = None,
    model: str | None = None,
    input_payload: Any | None = None,
    metadata: dict[str, Any] | None = None,
) -> Iterator[Any]:
    """
    Start a Langfuse observation safely.

    If Langfuse is disabled or unavailable, this yields a no-op observation.
    Runtime tracing failures are logged and never propagated to application flows.
    """
    client = get_langfuse_client()
    if client is None:
        yield NOOP_OBSERVATION
        return

    yielded = False
    body_raised = False

    try:
        observation_kwargs: dict[str, Any] = {
            "name": name,
            "as_type": as_type,
        }
        if trace_context:
            observation_kwargs["trace_context"] = trace_context
        if model and as_type in {"generation", "embedding"}:
            observation_kwargs["model"] = model

        with client.start_as_current_observation(**observation_kwargs) as observation:
            if input_payload is not None or metadata:
                safe_update_observation(
                    observation,
                    input=input_payload,
                    metadata=metadata,
                )

            yielded = True
            try:
                yield observation
            except Exception:
                body_raised = True
                raise
    except Exception as exc:
        if body_raised:
            raise

        logger.debug("Langfuse observation setup failed (%s): %s", name, exc)
        if not yielded:
            yield NOOP_OBSERVATION


def safe_update_observation(observation: Any, **kwargs: Any) -> None:
    """Best-effort update for a Langfuse observation object."""
    if observation is None or observation is NOOP_OBSERVATION:
        return

    payload = {key: value for key, value in kwargs.items() if value is not None}
    if not payload:
        return

    try:
        observation.update(**payload)
    except Exception as exc:
        logger.debug("Langfuse observation update failed: %s", exc)


def safe_flush_langfuse() -> None:
    """Flush buffered Langfuse events without raising."""
    client = get_langfuse_client()
    if client is None:
        return

    try:
        client.flush()
    except Exception as exc:
        logger.warning("Langfuse flush failed: %s", exc)


def safe_shutdown_langfuse() -> None:
    """Shutdown Langfuse background workers without raising."""
    client = get_langfuse_client()
    if client is None:
        return

    try:
        client.shutdown()
    except Exception as exc:
        logger.warning("Langfuse shutdown failed: %s", exc)


def auth_check_langfuse() -> tuple[bool, str]:
    """Run Langfuse auth check and return (ok, detail) without raising."""
    if not settings.langfuse_enabled:
        return True, "Disabled"

    client = get_langfuse_client()
    if client is None:
        return False, get_langfuse_init_error() or "Langfuse client unavailable"

    try:
        result = client.auth_check()
        if result is False:
            return False, "Auth check returned false"
        return True, f"Connected — {settings.langfuse_host}"
    except Exception as exc:
        return False, str(exc)
