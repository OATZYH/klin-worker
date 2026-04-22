"""
LLM Client — async HTTP client for llama-server.

Communicates with external llama-server processes (managed by Tauri)
via OpenAI-compatible REST APIs:
    • chat/vision: ``settings.llama_server_url``
    • embeddings: ``settings.llama_embedding_server_url``

The singleton lifecycle is managed by FastAPI's lifespan in ``app/main.py``:
  • ``startup()``  — create httpx client, verify server reachability
  • ``shutdown()`` — close httpx client
"""

import asyncio
import json
import logging
import queue
import threading
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Iterator

import httpx

from app.core.ai_exceptions import AiCapabilityUnavailableError
from app.core.config import settings
from app.observability.tracing import (
    observe,
    sanitize_for_trace,
    sanitize_messages,
    start_as_current_observation,
    update_current_generation,
    update_current_span,
)

logger = logging.getLogger(__name__)


class LlmClient:
    """
    Async HTTP client for a local llama-server instance.

    All inference is natively async via httpx.  No synchronous methods are
    exposed — callers use ``achat``, ``achat_with_vision``, and ``aembed``
    directly.
    """

    def __init__(self) -> None:
        self._chat_client: httpx.AsyncClient | None = None
        self._embed_client: httpx.AsyncClient | None = None
        self._vision_supported: bool = True  # assume vision unless proven otherwise
        self._embeddings_supported: bool = False
        self._chat_server_reachable: bool = False
        self._embed_server_reachable: bool = False
        self._model_id: str = ""

    @staticmethod
    def _truncate_text_value(value: str, max_chars: int) -> str:
        """Apply a coarse character guard to reduce worst-case prompt size."""
        if max_chars <= 0 or len(value) <= max_chars:
            return value
        return value[:max_chars]

    def _apply_input_char_guard(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Trim text-bearing message content before sending requests to llama-server."""
        max_chars = settings.llm_input_max_chars
        if max_chars <= 0:
            return messages

        guarded_messages = deepcopy(messages)
        for message in guarded_messages:
            content = message.get("content")
            if isinstance(content, str):
                message["content"] = self._truncate_text_value(content, max_chars)
                continue

            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text_value = part.get("text")
                        if isinstance(text_value, str):
                            part["text"] = self._truncate_text_value(text_value, max_chars)
        return guarded_messages

    # ── Lifecycle ────────────────────────────────────────────────────────

    @observe(name="llm.startup", capture_input=False, capture_output=False)
    async def startup(self) -> None:
        """
        Create the httpx client and optionally check server reachability.

        Call once at app startup (lifespan event).
        """
        if self._chat_client is not None and self._embed_client is not None:
            return

        if self._chat_client is None:
            self._chat_client = httpx.AsyncClient(
                base_url=settings.llama_server_url,
                timeout=300.0,
            )
        if self._embed_client is None:
            self._embed_client = httpx.AsyncClient(
                base_url=settings.llama_embedding_server_url,
                timeout=300.0,
            )

        try:
            await self.ensure_general_available()
            logger.info("chat llama-server reachable  →  model: %s", self._model_id)
        except Exception as exc:
            logger.warning(
                "chat llama-server not reachable at %s — chat/vision AI will fail "
                "until the server is available. (%s)",
                settings.llama_server_url,
                exc,
            )

        try:
            await self.ensure_embedding_available(require_general_check=False)
            logger.info("embedding llama-server reachable")
        except AiCapabilityUnavailableError as exc:
            logger.warning("Embeddings support: false (%s)", exc.detail)

    @observe(name="llm.shutdown", capture_input=False, capture_output=False)
    async def shutdown(self) -> None:
        """Close the httpx client.  Call at app shutdown."""
        if self._chat_client is not None:
            await self._chat_client.aclose()
            self._chat_client = None

        if self._embed_client is not None:
            await self._embed_client.aclose()
            self._embed_client = None

        self._chat_server_reachable = False
        self._embed_server_reachable = False
        self._embeddings_supported = False
        self._model_id = ""
        logger.info("llama-server HTTP clients closed.")

    @property
    def is_ready(self) -> bool:
        """Whether general AI is currently available."""
        return (
            self._chat_client is not None
            and self._chat_server_reachable
            and bool(self._model_id)
        )

    # kept for backward-compat in case any code references is_loaded
    @property
    def is_loaded(self) -> bool:
        return self.is_ready

    @observe(name="llm.ensure_general", capture_input=False, capture_output=False)
    async def ensure_general_available(self) -> None:
        """Validate that the llama-server can handle general chat requests."""
        self._assert_chat_client_started()
        update_current_span(metadata={"server": settings.llama_server_url})

        try:
            resp = await self._chat_client.get("/models", timeout=10.0)  # type: ignore[union-attr]
            resp.raise_for_status()
            data = resp.json()
            models = data.get("data", [])
            self._chat_server_reachable = True

            if not models:
                self._model_id = ""
                self._vision_supported = False
                raise AiCapabilityUnavailableError(
                    "general",
                    "General AI is unavailable because llama-server has no loaded model.",
                )

            self._model_id = models[0].get("id", "unknown")
            _vision_keywords = ("vl", "vision", "multimodal", "mm")
            name_lower = self._model_id.lower()
            self._vision_supported = any(kw in name_lower for kw in _vision_keywords)
            update_current_span(output={"model": self._model_id, "vision_supported": self._vision_supported})
        except AiCapabilityUnavailableError:
            raise
        except Exception as exc:
            self._mark_general_unavailable()
            raise AiCapabilityUnavailableError(
                "general",
                "General AI is unavailable because llama-server cannot be reached.",
            ) from exc

    @observe(name="llm.ensure_embedding", capture_input=False, capture_output=False)
    async def ensure_embedding_available(
        self,
        *,
        require_general_check: bool = True,
    ) -> None:
        """Validate that the llama-server can handle embedding requests."""
        self._assert_embedding_client_started()

        if require_general_check:
            await self.ensure_general_available()

        try:
            resp = await self._embed_client.post(  # type: ignore[union-attr]
                "/embeddings",
                json={"input": ["health-check"]},
                timeout=15.0,
            )
            resp.raise_for_status()
            self._embed_server_reachable = True
            self._embeddings_supported = True
            update_current_span(output={"embeddings_supported": True})
        except Exception as exc:
            self._embed_server_reachable = False
            self._embeddings_supported = False
            raise AiCapabilityUnavailableError(
                "embedding",
                "Embedding AI is unavailable because the embeddings endpoint is not responding.",
            ) from exc

    # ── Chat completions ─────────────────────────────────────────────────

    @observe(name="llm.chat", as_type="generation", capture_input=False, capture_output=False)
    async def achat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ) -> str:
        """
        Run a chat completion via the llama-server ``/chat/completions`` endpoint.

        Returns the assistant message content as a plain string.
        """
        self._assert_chat_client_started()
        guarded_messages = self._apply_input_char_guard(messages)

        body: dict[str, Any] = {
            "messages": guarded_messages,
            "temperature": temperature,
            "max_tokens": max_tokens or settings.llm_output_max_tokens,
        }
        update_current_generation(
            input={"messages": sanitize_messages(guarded_messages)},
            model=self._model_id or None,
            model_parameters={
                "temperature": temperature,
                "max_tokens": max_tokens or settings.llm_output_max_tokens,
            },
        )

        try:
            resp = await self._chat_client.post("/chat/completions", json=body)  # type: ignore[union-attr]
            resp.raise_for_status()
            data = resp.json()
            self._chat_server_reachable = True
            content = data["choices"][0]["message"]["content"].strip()
            usage = data.get("usage") or {}
            usage_details = None
            if usage:
                usage_details = {
                    "input": int(usage.get("prompt_tokens", 0) or 0),
                    "output": int(usage.get("completion_tokens", 0) or 0),
                }
            update_current_generation(output=content, usage_details=usage_details)
            return content
        except Exception as exc:
            self._mark_general_unavailable()
            raise AiCapabilityUnavailableError(
                "general",
                "General AI is unavailable because llama-server cannot complete chat requests.",
            ) from exc

    def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ) -> Iterator[str]:
        """Synchronous token stream wrapper around `achat_stream()`."""
        output_queue: queue.Queue[str | Exception | object] = queue.Queue()
        done = object()

        async def _producer() -> None:
            try:
                async for token in self.achat_stream(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                ):
                    output_queue.put(token)
            except Exception as exc:
                output_queue.put(exc)
            finally:
                output_queue.put(done)

        def _run() -> None:
            asyncio.run(_producer())

        threading.Thread(target=_run, daemon=True).start()

        while True:
            item = output_queue.get()
            if item is done:
                break
            if isinstance(item, Exception):
                raise item
            if not isinstance(item, str):
                continue
            yield item

    async def achat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """Stream chat completion tokens from llama-server."""
        self._assert_chat_client_started()
        guarded_messages = self._apply_input_char_guard(messages)

        body: dict[str, Any] = {
            "messages": guarded_messages,
            "temperature": temperature,
            "max_tokens": max_tokens or settings.llm_output_max_tokens,
            "stream": True,
        }
        aggregated: list[str] = []
        first_token_at: datetime | None = None

        with start_as_current_observation(
            name="llm.chat_stream",
            as_type="generation",
            input={"messages": sanitize_messages(guarded_messages)},
            model=self._model_id or None,
            model_parameters={
                "temperature": temperature,
                "max_tokens": max_tokens or settings.llm_output_max_tokens,
                "stream": True,
            },
        ):
            try:
                async with self._chat_client.stream(  # type: ignore[union-attr]
                    "POST",
                    "/chat/completions",
                    json=body,
                ) as resp:
                    resp.raise_for_status()
                    self._chat_server_reachable = True

                    async for raw_line in resp.aiter_lines():
                        line = raw_line.strip()
                        if not line:
                            continue

                        payload = line
                        if payload.startswith("data:"):
                            payload = payload[len("data:"):].strip()

                        if payload == "[DONE]":
                            break

                        try:
                            data = json.loads(payload)
                        except json.JSONDecodeError:
                            continue

                        choices = data.get("choices", [])
                        if not choices:
                            continue

                        first_choice = choices[0]
                        delta = first_choice.get("delta") or {}
                        content = delta.get("content")

                        if not content:
                            message = first_choice.get("message") or {}
                            content = message.get("content")

                        if isinstance(content, str) and content:
                            aggregated.append(content)
                            if first_token_at is None:
                                first_token_at = datetime.now(timezone.utc)
                                update_current_generation(completion_start_time=first_token_at)
                            yield content

                update_current_generation(
                    output={
                        "content": "".join(aggregated),
                        "chunk_count": len(aggregated),
                    }
                )
            except Exception as exc:
                self._mark_general_unavailable()
                update_current_generation(level="ERROR", status_message=str(exc))
                raise AiCapabilityUnavailableError(
                    "general",
                    "General AI is unavailable because llama-server cannot stream chat requests.",
                ) from exc

    # ── Vision / multimodal ─────────────────────────────────────────────

    @observe(name="llm.chat_vision", as_type="generation", capture_input=False, capture_output=False)
    async def achat_with_vision(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ) -> str:
        """
        Chat completion that accepts OpenAI-style multimodal messages.

        If the server model supports vision, image_url content blocks are
        forwarded as-is.  Otherwise images are stripped and the request is
        retried as text-only.
        """
        self._assert_chat_client_started()

        if not self._vision_supported:
            return await self.achat(
                self._strip_images(messages),
                temperature=temperature,
                max_tokens=max_tokens,
            )

        try:
            guarded_messages = self._apply_input_char_guard(messages)
            update_current_generation(
                input={"messages": sanitize_messages(guarded_messages)},
                model=self._model_id or None,
                model_parameters={
                    "temperature": temperature,
                    "max_tokens": max_tokens or settings.llm_output_max_tokens,
                    "vision": True,
                },
            )
            body: dict[str, Any] = {
                "messages": guarded_messages,
                "temperature": temperature,
                "max_tokens": max_tokens or settings.llm_output_max_tokens,
            }
            resp = await self._chat_client.post("/chat/completions", json=body)  # type: ignore[union-attr]
            resp.raise_for_status()
            data = resp.json()
            self._chat_server_reachable = True
            content = data["choices"][0]["message"]["content"].strip()
            update_current_generation(output=content)
            return content
        except AiCapabilityUnavailableError:
            raise
        except Exception as exc:
            logger.warning(
                "Vision chat failed — falling back to text-only. (%s)", exc
            )
            self._vision_supported = False
            return await self.achat(
                self._strip_images(messages),
                temperature=temperature,
                max_tokens=max_tokens,
            )

    @property
    def supports_vision(self) -> bool:
        """Whether the server model accepts multimodal (image) messages."""
        return self.is_ready and self._vision_supported is True

    @property
    def supports_embeddings(self) -> bool:
        """Whether the server exposes a usable embeddings endpoint."""
        return (
            self._embed_client is not None
            and self._embed_server_reachable
            and self._embeddings_supported
        )

    @staticmethod
    def _strip_images(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
        """Remove image_url blocks from multimodal messages, keeping text."""
        cleaned: list[dict[str, str]] = []
        for msg in messages:
            if msg is None:
                continue
            content = msg.get("content")
            if isinstance(content, list):
                text_parts = [
                    part["text"]
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                ]
                cleaned.append({
                    "role": msg["role"],
                    "content": "\n".join(text_parts) or "(image content — vision model required)",
                })
            elif isinstance(content, str):
                cleaned.append({"role": msg["role"], "content": content})
        return cleaned

    # ── Embeddings ───────────────────────────────────────────────────────

    async def aembed(self, texts: list[str]) -> list[list[float]]:
        """
        Generate embeddings via the llama-server ``/embeddings`` endpoint.

        Returns a list of float vectors, one per input text.

        Note: Some models return per-token embeddings as a 2D list per data
        item (shape [n_tokens, n_embd]).  We detect this and mean-pool the
        token vectors into a single document vector.
        """
        self._assert_embedding_client_started()

        body: dict[str, Any] = {"input": texts}
        with start_as_current_observation(
            name="llm.embed",
            as_type="embedding",
            input={"texts": sanitize_for_trace(texts), "text_count": len(texts)},
            model="llama-embedding-server",
        ) as observation:
            try:
                resp = await self._embed_client.post("/embeddings", json=body)  # type: ignore[union-attr]
                resp.raise_for_status()
                resp_json = resp.json()
                # llama-server may return a plain list or the OpenAI-compat {"data": [...]} wrapper
                data = resp_json if isinstance(resp_json, list) else resp_json["data"]
                self._embed_server_reachable = True
                self._embeddings_supported = True
            except Exception as exc:
                self._embed_server_reachable = False
                self._embeddings_supported = False
                if observation is not None:
                    observation.update(level="ERROR", status_message=str(exc))
                raise AiCapabilityUnavailableError(
                    "embedding",
                    "Embedding AI is unavailable because llama-server cannot complete embedding requests.",
                ) from exc

            pooled: list[list[float]] = []
            for item in data:
                emb = item["embedding"]
                if emb and isinstance(emb[0], list):
                    n_tokens = len(emb)
                    dim = len(emb[0])
                    avg = [
                        sum(emb[t][d] for t in range(n_tokens)) / n_tokens
                        for d in range(dim)
                    ]
                    pooled.append(avg)
                else:
                    pooled.append(emb)

            if observation is not None:
                observation.update(output={"vector_count": len(pooled)})
            return pooled

    # ── Internals ────────────────────────────────────────────────────────

    def _assert_chat_client_started(self) -> None:
        if self._chat_client is None:
            raise AiCapabilityUnavailableError(
                "general",
                "AI is unavailable because the LLM client is not initialised.",
            )

    def _assert_embedding_client_started(self) -> None:
        if self._embed_client is None:
            raise AiCapabilityUnavailableError(
                "embedding",
                "Embedding AI is unavailable because the LLM client is not initialised.",
            )

    def _mark_general_unavailable(self) -> None:
        self._chat_server_reachable = False
        self._model_id = ""
        self._vision_supported = False


# ── Module-level singleton ───────────────────────────────────────────────

llm_client = LlmClient()
