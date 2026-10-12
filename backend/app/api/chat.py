import logging

from fastapi import APIRouter, Depends
from sse_starlette.sse import EventSourceResponse

from app.core.auth import get_customer_context
from app.schemas.chat import ChatRequest
from app.services.llm import llm

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("")
async def chat(
    req: ChatRequest,
    context: str = Depends(get_customer_context)
):
    async def events():
        try:
            async for token in llm.stream(req.messages, context):
                yield {"data": token}

            yield {"event": "done", "data": ""}

        except Exception:
            logger.exception("LLM streaming failed")
            yield {
                "event": "error",
                "data": "Unable to generate a response."
            }

    return EventSourceResponse(events())
