"""Thin Langfuse tracing helpers with safe no-op behavior."""

from __future__ import annotations

import inspect
import logging
import re
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable, Iterator, Literal, TypeVar, cast

from langfuse import Langfuse, propagate_attributes as langfuse_propagate_attributes

from app.core.config import settings

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])
ObservationKind = Literal["span", "generation"]

_langfuse_client: Langfuse | None = None
_current_request_trace_id: ContextVar[str | None] = ContextVar(
    "current_request_trace_id",
    default=None,
)
_suppress_trace_text_payloads: ContextVar[bool] = ContextVar(
    "suppress_trace_text_payloads",
    default=False,
)

_KEY_REDACTIONS = {
    "authorization",
    "api_key",
    "apikey",
    "password",
    "secret",
    "secret_key",
    "token",
}


def _redact_string(value: str) -> str:
    redacted = re.sub(r"\b(?:pk|sk)-lf-[A-Za-z0-9\-]+\b", "[REDACTED_LANGFUSE_KEY]", value)
    if redacted.startswith("data:") and ";base64," in redacted:
        return "[REDACTED_BINARY_DATA_URL]"

    max_chars = settings.langfuse_max_payload_chars
    if max_chars > 0 and len(redacted) > max_chars:
        return f"{redacted[:max_chars]}... [truncated {len(redacted) - max_chars} chars]"
    return redacted


def _mask_value(data: Any, **_: Any) -> Any:
    if isinstance(data, str):
        return _redact_string(data)
    if isinstance(data, dict):
        masked: dict[str, Any] = {}
        for key, value in data.items():
            key_text = str(key)
            if key_text.lower() in _KEY_REDACTIONS:
                masked[key_text] = "[REDACTED]"
            elif key_text == "image_url":
                masked[key_text] = "[REDACTED_IMAGE_URL]"
            else:
                masked[key_text] = _mask_value(value)
        return masked
    if isinstance(data, list):
        return [_mask_value(item) for item in data]
    if isinstance(data, tuple):
        return [_mask_value(item) for item in data]
    return data


def sanitize_for_trace(value: Any) -> Any:
    return _mask_value(value)


def sanitize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role", "user"))
        content = message.get("content")
        if isinstance(content, list):
            parts: list[dict[str, Any]] = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "image_url":
                    parts.append({"type": "image_url", "image_url": "[REDACTED_IMAGE_URL]"})
                    continue
                parts.append(sanitize_for_trace(part))
            sanitized.append({"role": role, "content": parts})
            continue
        sanitized.append({"role": role, "content": sanitize_for_trace(content)})
    return sanitized


def _filter_call_args(func: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    try:
        bound = inspect.signature(func).bind_partial(*args, **kwargs)
    except Exception:
        return {}

    ignored = {"self", "cls", "db", "request", "response"}
    return {
        key: sanitize_for_trace(value)
        for key, value in bound.arguments.items()
        if key not in ignored
    }


def is_tracing_enabled() -> bool:
    return _langfuse_client is not None and settings.langfuse_enabled


def init_langfuse() -> Langfuse | None:
    global _langfuse_client

    if _langfuse_client is not None:
        return _langfuse_client

    if not settings.langfuse_enabled:
        logger.info("Langfuse tracing disabled.")
        return None

    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        logger.warning("Langfuse enabled but credentials missing. Tracing disabled.")
        return None

    _langfuse_client = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
        debug=settings.langfuse_debug,
        tracing_enabled=settings.langfuse_enabled,
        sample_rate=settings.langfuse_sample_rate,
        environment=settings.langfuse_environment,
        release=settings.app_version,
        mask=_mask_value,
    )
    logger.info("Langfuse tracing initialized for %s", settings.langfuse_host)
    return _langfuse_client


def flush_langfuse() -> None:
    if _langfuse_client is None:
        return
    try:
        _langfuse_client.flush()
    except Exception:
        logger.warning("Langfuse flush failed.", exc_info=True)


def get_current_trace_id() -> str | None:
    if _langfuse_client is not None:
        try:
            trace_id = _langfuse_client.get_current_trace_id()
            if trace_id:
                return trace_id
        except Exception:
            logger.debug("Unable to read current Langfuse trace id.", exc_info=True)
    return _current_request_trace_id.get()


def create_trace_id(seed: str | None = None) -> str | None:
    if _langfuse_client is None:
        return None
    return _langfuse_client.create_trace_id(seed=seed)


def set_current_request_trace_id(trace_id: str | None) -> object:
    return _current_request_trace_id.set(trace_id)


def reset_current_request_trace_id(token: object) -> None:
    _current_request_trace_id.reset(token)


def is_trace_text_payload_suppressed() -> bool:
    return _suppress_trace_text_payloads.get()


@contextmanager
def suppress_trace_text_payloads() -> Iterator[None]:
    token = _suppress_trace_text_payloads.set(True)
    try:
        yield
    finally:
        _suppress_trace_text_payloads.reset(token)


@contextmanager
def propagate_trace_attributes(
    *,
    user_id: str | None = None,
    session_id: str | None = None,
    metadata: dict[str, str] | None = None,
    version: str | None = None,
    tags: list[str] | None = None,
    trace_name: str | None = None,
) -> Iterator[None]:
    if _langfuse_client is None:
        with nullcontext():
            yield
        return

    with langfuse_propagate_attributes(
        user_id=user_id,
        session_id=session_id,
        metadata=metadata,
        version=version,
        tags=tags,
        trace_name=trace_name,
    ):
        yield


@contextmanager
def start_as_current_observation(
    *,
    name: str,
    as_type: str = "span",
    trace_context: dict[str, Any] | None = None,
    input: Any = None,
    output: Any = None,
    metadata: Any = None,
    model: str | None = None,
    model_parameters: dict[str, Any] | None = None,
    completion_start_time: datetime | None = None,
    usage_details: dict[str, int] | None = None,
) -> Iterator[Any]:
    if _langfuse_client is None:
        with nullcontext(None) as observation:
            yield observation
        return

    with _langfuse_client.start_as_current_observation(
        name=name,
        as_type=as_type,
        trace_context=trace_context,
        input=sanitize_for_trace(input),
        output=sanitize_for_trace(output),
        metadata=sanitize_for_trace(metadata),
        model=model,
        model_parameters=sanitize_for_trace(model_parameters),
        completion_start_time=completion_start_time,
        usage_details=usage_details,
    ) as observation:
        yield observation


def update_current_span(
    *,
    name: str | None = None,
    input: Any = None,
    output: Any = None,
    metadata: Any = None,
    level: str | None = None,
    status_message: str | None = None,
) -> None:
    if _langfuse_client is None:
        return
    _langfuse_client.update_current_span(
        name=name,
        input=sanitize_for_trace(input),
        output=sanitize_for_trace(output),
        metadata=sanitize_for_trace(metadata),
        level=cast(Any, level),
        status_message=status_message,
    )


def update_current_generation(
    *,
    name: str | None = None,
    input: Any = None,
    output: Any = None,
    metadata: Any = None,
    level: str | None = None,
    status_message: str | None = None,
    completion_start_time: datetime | None = None,
    model: str | None = None,
    model_parameters: dict[str, Any] | None = None,
    usage_details: dict[str, int] | None = None,
) -> None:
    if _langfuse_client is None:
        return
    _langfuse_client.update_current_generation(
        name=name,
        input=sanitize_for_trace(input),
        output=sanitize_for_trace(output),
        metadata=sanitize_for_trace(metadata),
        level=cast(Any, level),
        status_message=status_message,
        completion_start_time=completion_start_time,
        model=model,
        model_parameters=sanitize_for_trace(model_parameters),
        usage_details=usage_details,
    )


def update_current_observation(
    *,
    as_type: ObservationKind = "span",
    name: str | None = None,
    input: Any = None,
    output: Any = None,
    metadata: Any = None,
    level: str | None = None,
    status_message: str | None = None,
    completion_start_time: datetime | None = None,
    model: str | None = None,
    model_parameters: dict[str, Any] | None = None,
    usage_details: dict[str, int] | None = None,
) -> None:
    """Update the current Langfuse observation using the correct SDK method."""
    if as_type == "generation":
        update_current_generation(
            name=name,
            input=input,
            output=output,
            metadata=metadata,
            level=level,
            status_message=status_message,
            completion_start_time=completion_start_time,
            model=model,
            model_parameters=model_parameters,
            usage_details=usage_details,
        )
        return

    update_current_span(
        name=name,
        input=input,
        output=output,
        metadata=metadata,
        level=level,
        status_message=status_message,
    )


def observe(
    func: F | None = None,
    *,
    name: str | None = None,
    as_type: str = "span",
    capture_input: bool = True,
    capture_output: bool = True,
) -> F | Callable[[F], F]:
    def decorator(inner: F) -> F:
        observation_name = name or inner.__qualname__

        if inspect.isasyncgenfunction(inner):
            return inner

        if inspect.iscoroutinefunction(inner):
            @wraps(inner)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                if _langfuse_client is None:
                    return await inner(*args, **kwargs)

                payload = _filter_call_args(inner, args, kwargs) if capture_input else None
                with start_as_current_observation(
                    name=observation_name,
                    as_type=as_type,
                    input=payload,
                ):
                    try:
                        result = await inner(*args, **kwargs)
                    except Exception as exc:
                        update_current_observation(
                            as_type=cast(ObservationKind, as_type),
                            level="ERROR",
                            status_message=str(exc),
                            metadata={"exception_type": type(exc).__name__},
                        )
                        raise

                    if capture_output:
                        update_current_observation(
                            as_type=cast(ObservationKind, as_type),
                            output=result,
                        )
                    return result

            return cast(F, async_wrapper)

        @wraps(inner)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            if _langfuse_client is None:
                return inner(*args, **kwargs)

            payload = _filter_call_args(inner, args, kwargs) if capture_input else None
            with start_as_current_observation(
                name=observation_name,
                as_type=as_type,
                input=payload,
            ):
                try:
                    result = inner(*args, **kwargs)
                except Exception as exc:
                    update_current_observation(
                        as_type=cast(ObservationKind, as_type),
                        level="ERROR",
                        status_message=str(exc),
                        metadata={"exception_type": type(exc).__name__},
                    )
                    raise
                if capture_output:
                    update_current_observation(
                        as_type=cast(ObservationKind, as_type),
                        output=result,
                    )
                return result

        return cast(F, sync_wrapper)

    if func is not None:
        return decorator(func)
    return decorator


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
