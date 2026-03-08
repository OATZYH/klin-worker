"""
LLM Client — async HTTP client for llama-server.

Communicates with an external llama-server process (managed by Tauri)
via its OpenAI-compatible REST API at ``settings.llama_server_url``.

The singleton lifecycle is managed by FastAPI's lifespan in ``app/main.py``:
  • ``startup()``  — create httpx client, verify server reachability
  • ``shutdown()`` — close httpx client
"""

import logging
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class LlmClient:
    """
    Async HTTP client for a local llama-server instance.

    All inference is natively async via httpx.  No synchronous methods are
    exposed — callers use ``achat``, ``achat_with_vision``, and ``aembed``
    directly.
    """

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._vision_supported: bool = True  # assume vision unless proven otherwise
        self._embeddings_supported: bool = True
        self._model_id: str = ""

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def startup(self) -> None:
        """
        Create the httpx client and optionally check server reachability.

        Call once at app startup (lifespan event).
        """
        if self._client is not None:
            return

        self._client = httpx.AsyncClient(
            base_url=settings.llama_server_url,
            timeout=300.0,
        )

        # Probe the server to log reachability and detect the loaded model
        try:
            resp = await self._client.get("/models")
            resp.raise_for_status()
            data = resp.json()
            models = data.get("data", [])
            if models:
                self._model_id = models[0].get("id", "unknown")
                logger.info(
                    "llama-server reachable  →  model: %s", self._model_id
                )

                # Infer vision capability from model id
                _vision_keywords = ("vl", "vision", "multimodal", "mm")
                name_lower = self._model_id.lower()
                self._vision_supported = any(
                    kw in name_lower for kw in _vision_keywords
                )
                logger.info(
                    "Vision support: %s (model: %s)",
                    self._vision_supported,
                    self._model_id,
                )

                try:
                    embed_resp = await self._client.post(
                        "/embeddings",
                        json={"input": ["health-check"]},
                    )
                    embed_resp.raise_for_status()
                    self._embeddings_supported = True
                    logger.info("Embeddings support: true")
                except Exception as exc:
                    self._embeddings_supported = False
                    logger.warning(
                        "Embeddings support: false (%s)",
                        exc,
                    )
            else:
                logger.warning(
                    "llama-server reachable but no models loaded."
                )
        except Exception as exc:
            logger.warning(
                "llama-server not reachable at %s — AI features will fail "
                "until the server is available. (%s)",
                settings.llama_server_url,
                exc,
            )

    async def shutdown(self) -> None:
        """Close the httpx client.  Call at app shutdown."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            logger.info("llama-server HTTP client closed.")

    @property
    def is_ready(self) -> bool:
        """Whether the HTTP client has been initialised."""
        return self._client is not None

    # kept for backward-compat in case any code references is_loaded
    @property
    def is_loaded(self) -> bool:
        return self.is_ready

    # ── Chat completions ─────────────────────────────────────────────────

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
        self._assert_ready()

        body: dict[str, Any] = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens or settings.max_token_size,
        }

        resp = await self._client.post("/chat/completions", json=body)  # type: ignore[union-attr]
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()

    # ── Vision / multimodal ─────────────────────────────────────────────

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
        self._assert_ready()

        # Fast path: we know the model lacks vision
        if not self._vision_supported:
            return await self.achat(
                self._strip_images(messages),
                temperature=temperature,
                max_tokens=max_tokens,
            )

        try:
            body: dict[str, Any] = {
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens or settings.max_token_size,
            }
            resp = await self._client.post("/chat/completions", json=body)  # type: ignore[union-attr]
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
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
        return self._vision_supported is True

    @property
    def supports_embeddings(self) -> bool:
        """Whether the server exposes a usable embeddings endpoint."""
        return self._embeddings_supported is True

    @staticmethod
    def _strip_images(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
        """Remove image_url blocks from multimodal messages, keeping text."""
        cleaned: list[dict[str, str]] = []
        for msg in messages:
            if msg is None:
                continue
            content = msg.get("content")
            if isinstance(content, list):
                # Extract only text parts from multimodal content array
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
        self._assert_ready()

        if not self._embeddings_supported:
            raise RuntimeError(
                "llama-server embeddings endpoint is unavailable. "
                "Start llama-server with embeddings enabled."
            )

        body: dict[str, Any] = {"input": texts}
        resp = await self._client.post("/embeddings", json=body)  # type: ignore[union-attr]
        resp.raise_for_status()
        data = resp.json()["data"]

        pooled: list[list[float]] = []
        for item in data:
            emb = item["embedding"]
            # Detect 2D per-token embeddings: list of lists
            if emb and isinstance(emb[0], list):
                # Mean-pool token vectors → single document vector
                n_tokens = len(emb)
                dim = len(emb[0])
                avg = [
                    sum(emb[t][d] for t in range(n_tokens)) / n_tokens
                    for d in range(dim)
                ]
                pooled.append(avg)
            else:
                # Already a 1D vector
                pooled.append(emb)
        return pooled

    # ── Internals ────────────────────────────────────────────────────────

    def _assert_ready(self) -> None:
        if self._client is None:
            raise RuntimeError(
                "LLM client is not initialised. Call LlmClient.startup() first."
            )


# ── Module-level singleton ───────────────────────────────────────────────

llm_client = LlmClient()
