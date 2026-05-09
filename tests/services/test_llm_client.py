from __future__ import annotations

import asyncio
import json

import httpx

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