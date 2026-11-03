"""Embeddings for hybrid knowledge search (KNOWLEDGE_SEARCH=hybrid, migration 0099).

The client has its own key and endpoint (EMBEDDING_API_KEY, EMBEDDING_BASE_URL; any OpenAI-compatible embeddings API,
OpenRouter by default), so the chat model's LLM_API_KEY is never sent here. Questions are redacted again before they
leave (the guardrail has already redacted them), so a secret typed into a question never reaches the provider.
Section texts come from approved knowledge and are sent as written (scripts/embed_knowledge.py).
The SDK retries once on timeouts, connection errors, rate limits and 5xx; every failure raises EmbeddingError.
"""
import asyncio
import math
import time
from dataclasses import dataclass
from functools import lru_cache
from openai import AsyncOpenAI
from app.core.config import settings
from app.core.observability import span
from app.services.privacy import redact


class EmbeddingError(RuntimeError):
    """No usable vectors: not configured, the request failed or the reply was malformed. `reason` is an audit-safe token."""
    def __init__(self,reason: str):
        super().__init__(reason)
        self.reason=reason


@dataclass(frozen=True)
class Embedded:
    vectors: list[list[float]]
    model: str
    tokens: int | None
    ms: int


def new_client() -> AsyncOpenAI:
    key=settings.embedding_api_key
    if key is None or not key.get_secret_value():raise EmbeddingError('not_configured')
    return AsyncOpenAI(api_key=key.get_secret_value(),base_url=settings.embedding_base_url,timeout=settings.embedding_timeout_s,max_retries=1)


@lru_cache(maxsize=1)
def client() -> AsyncOpenAI:
    """The app's client, kept open so requests reuse the connection."""
    return new_client()


async def embed(texts: list[str],model: str | None=None,api: AsyncOpenAI | None=None) -> Embedded:
    """One request for all texts; vectors come back in input order, all the same size."""
    model=model or settings.embedding_model
    start=time.perf_counter()
    with span('embed','embedding',model=model,count=len(texts)) as attrs:
        try:response=await (api or client()).embeddings.create(model=model,input=texts)
        except EmbeddingError:raise
        except Exception as error:
            attrs['cause']=type(error).__name__[:64]
            raise EmbeddingError('request_failed:'+type(error).__name__[:48]) from error
        vectors=[list(item.embedding) for item in sorted(response.data,key=lambda item:item.index)]
        if len(vectors)!=len(texts) or not vectors or any(not 0<len(v)<=4096 or len(v)!=len(vectors[0]) for v in vectors) \
                or not all(math.isfinite(x) for v in vectors for x in v):
            raise EmbeddingError('bad_response')
        tokens=getattr(response.usage,'prompt_tokens',None) if response.usage else None
        ms=round((time.perf_counter()-start)*1000)
        attrs.update(dimensions=len(vectors[0]),tokens=tokens,ms=ms)
    return Embedded(vectors,model,tokens,ms)


async def embed_query(text: str,model: str | None=None,api: AsyncOpenAI | None=None) -> Embedded:
    return await embed([redact(text)[0]],model,api)


class Runner:
    """Embeddings for scripts and the retrieval evaluation, outside the app: one event loop and one open client for the
    whole run, so request times match the app's reused connection."""
    def __init__(self) -> None:
        self.api=new_client()
        self.loop=asyncio.new_event_loop()

    def embed(self,texts: list[str],model: str | None=None) -> Embedded:
        return self.loop.run_until_complete(embed(texts,model,self.api))

    def embed_query(self,text: str,model: str | None=None) -> Embedded:
        return self.loop.run_until_complete(embed_query(text,model,self.api))

    def close(self) -> None:
        self.loop.run_until_complete(self.api.close())
        self.loop.close()
