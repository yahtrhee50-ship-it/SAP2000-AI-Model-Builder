"""OpenAI GPT provider."""
from __future__ import annotations
from typing import AsyncIterator
from openai import AsyncOpenAI
from .base import AIProvider

_MODEL = "gpt-4o"


class OpenAIProvider(AIProvider):
    name = "openai"

    def __init__(self, api_key: str):
        self._client = AsyncOpenAI(api_key=api_key)

    async def chat_stream(
        self,
        messages: list[dict],
        system_prompt: str,
    ) -> AsyncIterator[str]:
        full_messages = [{"role": "system", "content": system_prompt}] + messages
        stream = await self._client.chat.completions.create(
            model=_MODEL,
            messages=full_messages,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    async def chat(
        self,
        messages: list[dict],
        system_prompt: str,
    ) -> str:
        full_messages = [{"role": "system", "content": system_prompt}] + messages
        response = await self._client.chat.completions.create(
            model=_MODEL,
            messages=full_messages,
        )
        return response.choices[0].message.content
