"""Connect guarded chat turns to owned durable storage. Runtime credentials never enter saved replies."""
from fastapi import Request
from pydantic import BaseModel
from app.api.conversations import access_for
from app.core import data_api
from app.core.config import settings
from app.core.errors import ApiError
from app.schemas.chat import ChatRequest,Message
from app.schemas.contracts import RequestContext,RiskAssessment
from app.schemas.conversations import ConversationAccess,StoredError,StoredFailure,TurnInput,TurnResponse
from app.schemas.events import AnswerEvent
from app.services import conversations
from app.services.guardrail import assess_request


class PreparedChat(BaseModel):
    request: ChatRequest
    risk: RiskAssessment
    access: ConversationAccess
    turn: TurnResponse
    history_trimmed: bool


class SavedReplyFailed(RuntimeError):
    """A persisted failure or cancellation must never be presented as an AI answer."""


async def prepare(req: ChatRequest,request: Request,ctx: RequestContext) -> PreparedChat:
    if req.conversation_id is None or req.turn_id is None or req.revision is None:
        raise ApiError(422,'conversation_required','Create or reopen a saved chat before sending this message.')
    access=access_for(req.conversation_id,request,ctx)
    with data_api.connect(ctx) as db:
        history=await conversations.load(access,ctx,db)
        if history.session.mode!=req.mode or history.session.language!=req.language:
            raise ApiError(409,'conversation_conflict','Start a new chat to change its information mode or answer language.')
        stored=[item for item in history.messages if not isinstance(item.payload,StoredFailure)]
        previous=[Message(role=item.role,content=item.redacted_text[:4000]) for item in stored[-28:]]
        while previous and previous[0].role!='user':previous=previous[1:]
        canonical=req.model_copy(update={'messages':[*previous,req.messages[-1]]})
        risk=assess_request(canonical)
        if settings.pipeline=='v2' and req.mode=='personal' and risk.fixed_response is None:
            raise ApiError(503,'personal_unavailable','Personal demo records are unavailable. Select General help to continue.')
        turn=await conversations.start_turn(TurnInput(turn_id=req.turn_id,revision=req.revision,
            user_text=req.messages[-1].content,priority=risk.priority),access,ctx,db)
        if turn.response is not None and turn.turn.revision!=history.session.revision:
            raise ApiError(409,'conversation_conflict','This chat has newer messages. Reopen its latest history before retrying.')
    return PreparedChat(request=canonical,risk=risk,access=access,turn=turn,
        history_trimmed=history.history_trimmed or len(stored)>len(previous) or any(len(item.redacted_text)>4000 for item in stored))


async def finish(prepared: PreparedChat,turn: TurnResponse,reply: AnswerEvent | StoredFailure,ctx: RequestContext) -> TurnResponse:
    with data_api.connect(ctx) as db:return await conversations.finish_turn(turn,reply,prepared.access,ctx,db)


def metadata(prepared: PreparedChat,turn: TurnResponse) -> dict[str,str | int | None]:
    return {'conversation_id':str(prepared.access.session_id),'revision':turn.turn.revision,
        'active_turn_id':str(turn.turn.turn_id) if turn.turn.state=='processing' else None}


def unavailable() -> StoredFailure:
    return StoredFailure(error=StoredError(code='assistant_unavailable',
        message='The assistant is temporarily unavailable. Please retry or use your bank’s official support channel.'))
