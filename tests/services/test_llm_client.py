from __future__ import annotations

import asyncio
import json

import httpx

from app.core.ai_exceptions import AiCapabilityUnavailableError
from app.core.config import settings
from app.services.ai.llm_client import LlmClient


def test_achat_disables_thinking_in_chat_requests() -> None:
    async def run() -> None:
        captured_body: dict[str, object] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_body
            captured_body = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "short summary",
                                "reasoning_content": "hidden reasoning",
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 4},
                },
            )

        client = LlmClient()
        client._chat_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="http://testserver",
        )

        try:
            result = await client.achat(
                [{"role": "user", "content": "summarise this"}],
                max_tokens=32,
            )
        finally:
            await client._chat_client.aclose()

        assert result == "short summary"
        assert captured_body["chat_template_kwargs"] == {"enable_thinking": False}
        assert captured_body["max_tokens"] == 32

    asyncio.run(run())


def test_aembed_rejects_unexpected_embedding_dimension() -> None:
    async def run() -> None:
        original_dim = settings.embedding_dim_size
        settings.embedding_dim_size = 2

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"data": [{"embedding": [0.1, 0.2, 0.3]}]},
            )

        client = LlmClient()
        client._embed_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="http://testserver",
        )

        try:
            try:
                await client.aembed(["hello"])
            except AiCapabilityUnavailableError as exc:
                assert "dimension 3" in exc.detail
                assert "KLIN_EMBEDDING_DIM_SIZE is 2" in exc.detail
            else:
                raise AssertionError("Expected embedding dimension mismatch")
        finally:
            settings.embedding_dim_size = original_dim
            await client._embed_client.aclose()

    asyncio.run(run())


def test_aembed_truncates_inputs_to_embedding_budget() -> None:
    async def run() -> None:
        original_dim = settings.embedding_dim_size
        original_chunk_size = settings.rag_chunk_token_size
        settings.embedding_dim_size = 2
        settings.rag_chunk_token_size = 4
        captured_body: dict[str, object] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_body
            captured_body = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={"data": [{"embedding": [0.1, 0.2]}]},
            )

        client = LlmClient()
        client._embed_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="http://testserver",
        )

        try:
            await client.aembed(["one two three four five six"])
        finally:
            settings.embedding_dim_size = original_dim
            settings.rag_chunk_token_size = original_chunk_size
            await client._embed_client.aclose()

        assert captured_body["input"] == ["one two three four"]

    asyncio.run(run())


def test_llm_requests_are_serialized() -> None:
    async def run() -> None:
        original_dim = settings.embedding_dim_size
        original_max_concurrent = settings.llama_max_concurrent_requests
        settings.embedding_dim_size = 2
        settings.llama_max_concurrent_requests = 1
        active_requests = 0
        max_active_requests = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal active_requests, max_active_requests
            active_requests += 1
            max_active_requests = max(max_active_requests, active_requests)
            await asyncio.sleep(0.01)
            active_requests -= 1

            if request.url.path == "/embeddings":
                return httpx.Response(
                    200,
                    json={"data": [{"embedding": [0.1, 0.2]}]},
                )

            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "ok",
                            }
                        }
                    ],
                },
            )

        client = LlmClient()
        transport = httpx.MockTransport(handler)
        client._chat_client = httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        )
        client._embed_client = httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        )

        try:
            await asyncio.gather(
                client.achat([{"role": "user", "content": "hello"}]),
                client.aembed(["hello"]),
            )
        finally:
            settings.embedding_dim_size = original_dim
            settings.llama_max_concurrent_requests = original_max_concurrent
            await client._chat_client.aclose()
            await client._embed_client.aclose()

        assert max_active_requests == 1

    asyncio.run(run())
