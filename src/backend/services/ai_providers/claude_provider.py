"""Anthropic Claude AI provider."""
from __future__ import annotations
from typing import AsyncIterator
import anthropic
from .base import AIProvider

_MODEL = "claude-sonnet-4-6"


class ClaudeProvider(AIProvider):
    name = "claude"

    def __init__(self, api_key: str):
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    async def chat_stream(
        self,
        messages: list[dict],
        system_prompt: str,
    ) -> AsyncIterator[str]:
        async with self._client.messages.stream(
            model=_MODEL,
            max_tokens=2048,
            system=system_prompt,
            messages=messages,
        ) as stream:
            async for text in stream.text_stream:
                yield text

    async def chat(
        self,
        messages: list[dict],
        system_prompt: str,
    ) -> str:
        response = await self._client.messages.create(
            model=_MODEL,
            max_tokens=2048,
            system=system_prompt,
            messages=messages,
        )
        return response.content[0].text
