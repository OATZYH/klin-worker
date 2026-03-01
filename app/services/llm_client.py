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
        """
        self._assert_loaded()

        result = self._llm.create_embedding(input=texts)  # type: ignore[union-attr]
        return [item["embedding"] for item in result["data"]]  # type: ignore[return-value]

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
