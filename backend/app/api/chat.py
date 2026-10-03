from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from app.schemas.chat import ChatRequest
from app.services.llm import llm

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("")
async def chat(req: ChatRequest):
    async def events():
        async for token in llm.stream(req.messages):
            yield {"data": token}
        yield {"event": "done", "data": ""}

    return EventSourceResponse(events())
