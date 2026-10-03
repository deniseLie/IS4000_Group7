from collections.abc import AsyncIterator

from anthropic import AsyncAnthropic

from app.core.config import settings
from app.schemas.chat import Message


class LLMClient:
    """Thin wrapper so the provider can be swapped without touching routes."""

    def __init__(self) -> None:
        self._client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def stream(self, messages: list[Message]) -> AsyncIterator[str]:
        async with self._client.messages.stream(
            model=settings.model,
            max_tokens=settings.max_tokens,
            system=settings.system_prompt,
            messages=[m.model_dump() for m in messages],
        ) as stream:
            async for text in stream.text_stream:
                yield text


llm = LLMClient()
