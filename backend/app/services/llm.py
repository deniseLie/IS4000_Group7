
import os
from collections.abc import AsyncIterator

from dotenv import load_dotenv
from openai import AsyncOpenAI, OpenAIError

from app.core.config import settings
from app.schemas.chat import Message

load_dotenv(".env")


class LLMClient:
    """OpenAI-compatible LLM client supporting OpenRouter."""

    def __init__(self) -> None:
        api_key = os.getenv("LLM_API_KEY")
        base_url = os.getenv("LLM_BASE_URL")
        self._model = os.getenv("LLM_MODEL")

        if not all([api_key, base_url, self._model]):
            raise ValueError("Missing LLM_API_KEY, LLM_BASE_URL or LLM_MODEL in .env")

        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=60.0,
            max_retries=2,
        )

    async def stream(
        self, messages: list[Message], context: str = ""
    ) -> AsyncIterator[str]:
        system = settings.system_prompt
        if context:
            system = f"{system}\n\n{context}"

        chat_messages = [{"role": "system", "content": system}]
        chat_messages += [m.model_dump() for m in messages]

        try:
            stream = await self._client.chat.completions.create(
                model=self._model,
                max_tokens=settings.max_tokens,
                messages=chat_messages,
                stream=True,
            )

            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

        except OpenAIError:
            raise RuntimeError(
                "The AI service is temporarily unavailable. Please try again."
            ) from None


llm = LLMClient()
