"""Owned conversation persistence. No JWT, raw model draft or unredacted user text enters storage.

SQL atomically stores each lifecycle transition and its audit event, with revision checks and a turn idempotency key.
Starting a turn persists its redacted question before a provider is called; finishing stores an answer or explicit error.
Loaded history remains untrusted content and must never establish tool permissions or action receipts.
"""
from postgrest import SyncPostgrestClient
from uuid import UUID,uuid4
from pydantic import JsonValue,TypeAdapter
from app.core.data_api import execute
from app.schemas.contracts import RequestContext
from app.schemas.conversations import ConversationAccess,ConversationHistory,ConversationSession,CreateConversation,GuestToken,StoredFailure,TurnInput,TurnResponse
from app.schemas.events import AnswerEvent
from app.services.privacy import redact


class ConversationIntegrityError(ValueError):
    """The database returned a different conversation or customer scope."""


def guest_token(access: ConversationAccess) -> str | None:
    return TypeAdapter(GuestToken).validate_python(access.guest_token.get_secret_value()) if access.guest_token is not None else None


def check_owner(session: ConversationSession,access: ConversationAccess,ctx: RequestContext) -> None:
    if session.session_id!=access.session_id or session.customer_id!=ctx.principal.customer_id:
        raise ConversationIntegrityError('The returned conversation does not match this session and customer scope')


async def create(query: CreateConversation,access: ConversationAccess,ctx: RequestContext,db: SyncPostgrestClient) -> ConversationSession:
    if query.session_id!=access.session_id:raise ConversationIntegrityError('Creation and access must name the same conversation')
    response=await execute(lambda:db.rpc('conversation_create',{'p_session_id':str(query.session_id),
        'p_mode':query.mode,'p_language':query.language,'p_guest_token':guest_token(access)}).execute(),'conversation.create')
    session=ConversationSession.model_validate(response.data)
    check_owner(session,access,ctx)
    return session


async def load(access: ConversationAccess,ctx: RequestContext,db: SyncPostgrestClient) -> ConversationHistory:
    if ctx.principal.kind=='guest':
        response=await execute(lambda:db.rpc('conversation_read_guest',{'p_session_id':str(access.session_id),
            'p_guest_token':guest_token(access)}).execute(),'conversation.read')
    else:response=await execute(lambda:db.rpc('conversation_read_customer',{'p_session_id':str(access.session_id)}).execute(),'conversation.read')
    history=ConversationHistory.model_validate(response.data)
    check_owner(history.session,access,ctx)
    if (history.active_turn is None)!=(history.session.active_turn_id is None):
        raise ConversationIntegrityError('Conversation activity does not match its pending turn')
    if history.active_turn is not None and (history.active_turn.session_id!=access.session_id
        or history.active_turn.customer_id!=ctx.principal.customer_id or history.active_turn.turn_id!=history.session.active_turn_id):
        raise ConversationIntegrityError('The active turn does not match the conversation and customer scope')
    if len(history.messages)>60 or any(a.sequence>=b.sequence for a,b in zip(history.messages,history.messages[1:])):
        raise ConversationIntegrityError('Stored conversation messages are not bounded in chronological order')
    return history


async def recent(ctx: RequestContext,db: SyncPostgrestClient) -> list[ConversationSession]:
    cid=ctx.principal.customer_id
    if ctx.principal.kind!='customer' or cid is None:raise PermissionError('Only linked customers can list saved conversations')
    response=await execute(lambda:db.schema('conversation').table('sessions').select('session_id,customer_id,mode,language,started_at,last_active_at,expires_at,revision,active_turn_id')
        .eq('customer_id',cid).order('last_active_at',desc=True).limit(50).execute(),'conversation.list')
    sessions=TypeAdapter(list[ConversationSession]).validate_python(response.data)
    if any(item.customer_id!=cid for item in sessions):raise ConversationIntegrityError('Conversation listing returned another customer scope')
    return sessions


def checked_turn(response: TurnResponse,access: ConversationAccess,ctx: RequestContext,turn_id: UUID) -> TurnResponse:
    turn=response.turn
    if turn.turn_id!=turn_id or turn.session_id!=access.session_id or turn.customer_id!=ctx.principal.customer_id:
        raise ConversationIntegrityError('The saved turn does not match this request and customer scope')
    return response


async def start_turn(query: TurnInput,access: ConversationAccess,ctx: RequestContext,db: SyncPostgrestClient) -> TurnResponse:
    run_id=uuid4()
    params: dict[str,JsonValue]={'p_session_id':str(access.session_id),'p_turn_id':str(query.turn_id),
        'p_revision':query.revision,'p_user_text':redact(query.user_text)[0],'p_run_id':str(run_id),
        'p_priority':query.priority,'p_guest_token':guest_token(access)}
    response=await execute(lambda:db.rpc('conversation_start_turn',params).execute(),'conversation.start')
    return checked_turn(TurnResponse.model_validate(response.data),access,ctx,query.turn_id)


async def finish_turn(turn: TurnResponse,reply: AnswerEvent | StoredFailure,access: ConversationAccess,ctx: RequestContext,db: SyncPostgrestClient) -> TurnResponse:
    if isinstance(reply,AnswerEvent) and (redact(reply.answer)[1] or any(redact(section.answer)[1] for section in reply.sections or [])):
        raise ConversationIntegrityError('An unredacted answer cannot enter conversation storage')
    payload=TypeAdapter(dict[str,JsonValue]).validate_python(reply.model_dump(mode='json'))
    response=await execute(lambda:db.rpc('conversation_finish_turn',{'p_session_id':str(access.session_id),
        'p_turn_id':str(turn.turn.turn_id),'p_run_id':str(turn.turn.run_id),'p_response':payload,'p_guest_token':guest_token(access)}).execute(),'conversation.finish')
    return checked_turn(TurnResponse.model_validate(response.data),access,ctx,turn.turn.turn_id)


async def cancel_turn(turn_id: UUID,access: ConversationAccess,ctx: RequestContext,db: SyncPostgrestClient) -> TurnResponse:
    response=await execute(lambda:db.rpc('conversation_cancel_turn',{'p_session_id':str(access.session_id),
        'p_turn_id':str(turn_id),'p_guest_token':guest_token(access)}).execute(),'conversation.cancel')
    return checked_turn(TurnResponse.model_validate(response.data),access,ctx,turn_id)
