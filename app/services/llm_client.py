"""
LLM Client — in-process llama-cpp-python model manager.

Loads a GGUF model once via llama-cpp-python's `Llama` class and provides
synchronous inference methods.  Services bridge to async using
`asyncio.to_thread()`.

The singleton lifecycle is managed by FastAPI's lifespan in `app/main.py`:
  • `startup()`  — load model into memory
  • `shutdown()` — release model resources
"""

import asyncio
import logging
from pathlib import Path
from typing import Any

from llama_cpp import Llama

from app.core.config import settings

logger = logging.getLogger(__name__)


class LlmClient:
    """
    Thin wrapper around a single llama-cpp-python `Llama` instance.

    All inference is synchronous (llama-cpp-python is blocking).
    Async callers should use the `a*` helper methods which delegate
    to `asyncio.to_thread()`.
    """

    def __init__(self) -> None:
        self._llm: Llama | None = None
        self._vision_supported: bool | None = None  # None = not yet tested

    # ── Lifecycle ────────────────────────────────────────────────────────

    def startup(self) -> None:
        """Load the GGUF model into memory.  Call once at app startup."""
        if self._llm is not None:
            return

        model_path = settings.model_path

        # Resolve relative paths against project root
        resolved = Path(model_path)
        if not resolved.is_absolute():
            resolved = Path(__file__).resolve().parent.parent.parent / model_path

        if not resolved.exists():
            raise FileNotFoundError(
                f"GGUF model not found: {resolved}\n"
                "Download the model and place it at the configured path.\n"
                f"  Config value: KLIN_LLAMACPP_MODEL_PATH={model_path}"
            )

        logger.info("Loading GGUF model: %s …", resolved)

        self._llm = Llama(
            model_path=str(resolved),
            n_ctx=settings.llamacpp_n_ctx,
            n_gpu_layers=settings.llamacpp_n_gpu_layers,
            n_batch=settings.llamacpp_n_batch,
            n_threads=settings.llamacpp_n_threads,
            embedding=True,           # enable embeddings
            verbose=settings.llamacpp_verbose,
        )

        logger.info(
            "Model loaded  →  n_ctx=%d  n_embd=%d  n_gpu_layers=%d",
            self._llm.n_ctx(),
            self._llm.n_embd(),
            settings.llamacpp_n_gpu_layers,
        )

    def shutdown(self) -> None:
        """Release model resources.  Call at app shutdown."""
        if self._llm is not None:
            self._llm.close()
            self._llm = None
            logger.info("GGUF model unloaded.")

    @property
    def is_loaded(self) -> bool:
        return self._llm is not None

    # ── Synchronous primitives ───────────────────────────────────────────

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ) -> str:
        """
        Run a chat completion and return the assistant message content.

        Raises RuntimeError if the model is not loaded.
        """
        self._assert_loaded()

        result: dict[str, Any] = self._llm.create_chat_completion(  # type: ignore[union-attr]
            messages=messages,  # type: ignore[arg-type]
            temperature=temperature,
            max_tokens=max_tokens or settings.max_token_size,
        )

        return result["choices"][0]["message"]["content"].strip()

    def embed(self, texts: list[str]) -> list[list[float]]:
        """
        Generate embeddings for a list of texts.

        Returns a list of float vectors, one per input text.

        Note: Some models (e.g. Gemma) return per-token embeddings as a 2D
        list per data item (shape [n_tokens, n_embd]) rather than a single
        pooled 1D vector. We detect this and mean-pool the token vectors
        into a single document vector.
        """
        self._assert_loaded()

        result = self._llm.create_embedding(input=texts)  # type: ignore[union-attr]
        data = result["data"]  # type: ignore[index]

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

    # ── Vision / multimodal ─────────────────────────────────────────────

    def chat_with_vision(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ) -> str:
        """
        Chat completion that accepts OpenAI-style multimodal messages.

        If the loaded model supports vision, image_url content blocks are
        forwarded as-is.  Otherwise the images are stripped and the request
        is retried as text-only so the pipeline never hard-fails.
        """
        self._assert_loaded()

        # Fast path: we already know the model lacks vision
        if self._vision_supported is False:
            return self.chat(
                self._strip_images(messages),
                temperature=temperature,
                max_tokens=max_tokens,
            )

        try:
            result: dict[str, Any] = self._llm.create_chat_completion(  # type: ignore[union-attr]
                messages=messages,  # type: ignore[arg-type]
                temperature=temperature,
                max_tokens=max_tokens or settings.max_token_size,
            )
            if self._vision_supported is None:
                self._vision_supported = True
                logger.info("Vision support confirmed — multimodal messages accepted.")
            return result["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            if self._vision_supported is None:
                self._vision_supported = False
                logger.warning(
                    "Model does not support vision input — falling back to text-only. "
                    "Swap to a vision-capable GGUF to enable image analysis. (%s)",
                    exc,
                )
            # Retry without image blocks
            return self.chat(
                self._strip_images(messages),
                temperature=temperature,
                max_tokens=max_tokens,
            )

    @property
    def supports_vision(self) -> bool:
        """Whether the loaded model accepts multimodal (image) messages."""
        return self._vision_supported is True

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

    # ── Async wrappers (for FastAPI / async services) ────────────────────

    async def achat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ) -> str:
        """Async wrapper around `chat()`."""
        return await asyncio.to_thread(
            self.chat,
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    async def achat_with_vision(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ) -> str:
        """Async wrapper around `chat_with_vision()`."""
        return await asyncio.to_thread(
            self.chat_with_vision,
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    async def aembed(self, texts: list[str]) -> list[list[float]]:
        """Async wrapper around `embed()`."""
        return await asyncio.to_thread(self.embed, texts)

    # ── Internals ────────────────────────────────────────────────────────

    def _assert_loaded(self) -> None:
        if self._llm is None:
            raise RuntimeError(
                "LLM model is not loaded. Call LlmClient.startup() first."
            )


# ── Module-level singleton ───────────────────────────────────────────────

llm_client = LlmClient()
