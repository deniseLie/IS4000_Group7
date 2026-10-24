"""Staff Action Service connector; no service-role key or direct table access.

The Data API verifies the access token; SQL requires an administrator-assigned support
role and aal2 on every call. Stable command UUIDs make retries return recorded receipts.
A card change requires the caller's claimed handoff and a temporarily blocked card.
"""
from uuid import UUID
from postgrest import SyncPostgrestClient
from postgrest.exceptions import APIError
from pydantic import JsonValue,TypeAdapter
from app.core.data_api import database_code,execute
from app.schemas.actions import ActionOutcome,CardId,StaffActionType
from app.schemas.staff import StaffHandoff
from app.services.actions.store import ActionIntegrityError


async def _call(rpc: str,params: dict[str,JsonValue],tool: str,db: SyncPostgrestClient) -> object:
    try:
        response=await execute(lambda:db.rpc(rpc,params).execute(),tool)
    except APIError as error:
        if database_code(error)=='42501':
            events: list[JsonValue]=[{'category':'policy','action':'policy.decided','outcome':'denied',
                'details':{'tool':tool,'reasons':['staff_access_denied'],'version':'staff_v1'}}]
            await execute(lambda:db.rpc('audit_record_batch',{'p_events':events}).execute(),'staff.denial_audit')
        raise
    return response.data


async def queue(limit: int,db: SyncPostgrestClient) -> list[StaffHandoff]:
    if isinstance(limit,bool) or not 1<=limit<=100:raise ValueError('Staff queue limit must be an integer from 1 to 100')
    payload=await _call('staff_handoff_queue',{'p_limit':limit},'handoff.queue',db)
    rows=TypeAdapter(list[StaffHandoff]).validate_python(payload)
    if len(rows)>limit or len({row.handoff_id for row in rows})!=len(rows):
        raise ActionIntegrityError('Staff queue exceeded its requested bound or repeated a handoff')
    if rows!=sorted(rows,key=lambda row:(row.priority,row.created_at,row.handoff_id)):
        raise ActionIntegrityError('Staff queue does not preserve priority then age ordering')
    return rows


async def _record(rpc: str,action_type: StaffActionType,command_id: UUID,handoff_id: UUID,params: dict[str,JsonValue],db: SyncPostgrestClient) -> ActionOutcome:
    payload=await _call(rpc,{'p_command_id':str(command_id),'p_handoff_id':str(handoff_id),**params},action_type,db)
    outcome=ActionOutcome.model_validate(payload)
    if outcome.command.command_id!=command_id or outcome.command.type!=action_type or outcome.command.target.handoff_id!=handoff_id:
        raise ActionIntegrityError('Staff result does not match its command, operation or handoff')
    return outcome


async def claim(command_id: UUID,handoff_id: UUID,db: SyncPostgrestClient) -> ActionOutcome:
    return await _record('staff_claim_handoff','handoff.claim',command_id,handoff_id,{},db)


async def close(command_id: UUID,handoff_id: UUID,db: SyncPostgrestClient) -> ActionOutcome:
    return await _record('staff_close_handoff','handoff.close',command_id,handoff_id,{},db)


async def unblock(command_id: UUID,handoff_id: UUID,card_id: str,db: SyncPostgrestClient) -> ActionOutcome:
    card=TypeAdapter(CardId).validate_python(card_id)
    outcome=await _record('staff_unblock_card','card.unblock',command_id,handoff_id,{'p_card_id':card},db)
    if outcome.command.target.card_id!=card:raise ActionIntegrityError('Staff unblock returned another card')
    return outcome


async def cancel(command_id: UUID,handoff_id: UUID,card_id: str,db: SyncPostgrestClient) -> ActionOutcome:
    card=TypeAdapter(CardId).validate_python(card_id)
    outcome=await _record('staff_cancel_card','card.cancel_permanently',command_id,handoff_id,{'p_card_id':card},db)
    if outcome.command.target.card_id!=card:raise ActionIntegrityError('Staff cancellation returned another card')
    return outcome


async def compliance_context(handoff_id: UUID,db: SyncPostgrestClient) -> dict | None:
    value=await _call('staff_compliance_context',{'p_handoff_id':str(handoff_id)},'staff.compliance_context',db)
    if value is not None and not isinstance(value,dict):raise ActionIntegrityError('Invalid compliance support context')
    return value
