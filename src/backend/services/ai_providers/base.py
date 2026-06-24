"""Abstract base class for AI providers."""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import AsyncIterator


class Message(dict):
    """Simple message dict wrapper: {role, content}."""
    pass


class AIProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def chat_stream(
        self,
        messages: list[dict],
        system_prompt: str,
    ) -> AsyncIterator[str]:
        """Yield text chunks from the AI response."""
        ...

    @abstractmethod
    async def chat(
        self,
        messages: list[dict],
        system_prompt: str,
    ) -> str:
        """Return a complete AI response."""
        ...
