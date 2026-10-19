"""Action Service: guarded proposal/confirmation, with durable receipts and no in-memory effects.

The agreed run_action(command, ctx, confirmed) interface delegates each write to one SQL transaction
(migrations 0017–0019). SQL independently checks JWT ownership and confirmation; executors, ledger,
outbox and audit commit together. API confirmation loads canonical parameters, never a client target.
"""
from uuid import UUID
from pydantic import JsonValue,TypeAdapter
from postgrest import SyncPostgrestClient
from app.core import data_api
from app.schemas.actions import ActionOutcome,ActionType
from app.schemas.contracts import ActionCommand,ProposedCommand,Receipt,RequestContext
from app.services.actions.store import ActionIntegrityError,checked,load
from app.services.authorization import PolicyDenied,require_permission

STUB=False


class ActionStateError(ValueError):
    """A rejected or expired command cannot produce an execution receipt."""


def canonical_target(command: ActionCommand) -> dict[str,JsonValue]:
    from app.services.actions.registry import EXECUTORS
    action_type=TypeAdapter(ActionType).validate_python(command.type)
    return TypeAdapter(dict[str,JsonValue]).validate_python(EXECUTORS[action_type](command).model_dump(mode='json'))


def receipt_for(outcome: ActionOutcome) -> Receipt | ProposedCommand:
    if outcome.receipt is not None:
        receipt=outcome.receipt
        return Receipt(receipt_id=receipt.receipt_id,command_id=receipt.command_id,outcome=receipt.outcome,audit_event_id=receipt.audit_event_id)
    if outcome.command.state=='proposed':return ProposedCommand(command_id=outcome.command.command_id,expires_at=outcome.command.expires_at)
    raise ActionStateError(outcome.command.state)


async def run_action(command: ActionCommand,ctx: RequestContext,confirmed: bool) -> Receipt | ProposedCommand:
    require_permission(ctx.principal,command.type,{'customer_id':ctx.principal.customer_id},ctx)
    target=canonical_target(command)
    if ctx.case_id is None:raise ActionIntegrityError('Actions require an owned conversation case')
    if confirmed and ctx.agent is not None:raise PolicyDenied('Agents may propose actions; only the customer can confirm them')
    with data_api.connect(ctx) as db:
        if confirmed:
            current=await load(command.command_id,ctx,db)
            stored=current.command
            if stored.case_id!=ctx.case_id or stored.type!=command.type or stored.idempotency_key!=command.idempotency_key:
                raise ActionIntegrityError('Confirmation must use the stored case, type and idempotency key')
            saved=ActionCommand(command_id=stored.command_id,idempotency_key=stored.idempotency_key,type=stored.type,
                target=stored.target.model_dump(mode='json'))
            if canonical_target(saved)!=target:raise ActionIntegrityError('Confirmation target differs from the stored proposal')
            response=await data_api.execute(lambda:db.rpc('action_confirm',{'p_command_id':str(command.command_id)}).execute(),'actions.confirm')
        else:
            if command.state!='proposed' or command.expires_at is not None:raise ActionIntegrityError('Proposal state and expiry are assigned by the database')
            response=await data_api.execute(lambda:db.rpc('action_propose',{'p_command_id':str(command.command_id),
                'p_case_id':str(ctx.case_id),'p_type':command.type,'p_target':target,'p_idempotency_key':command.idempotency_key}).execute(),'actions.propose')
    result=checked(response.data,ctx)
    if result.command.case_id!=ctx.case_id or result.command.type!=command.type or result.command.idempotency_key!=command.idempotency_key:
        raise ActionIntegrityError('The returned command does not match the proposed case and operation')
    return receipt_for(result)


async def reject_action(command_id: UUID,ctx: RequestContext,db: SyncPostgrestClient) -> ActionOutcome:
    current=await load(command_id,ctx,db)
    require_permission(ctx.principal,current.command.type,{'customer_id':ctx.principal.customer_id},ctx)
    if ctx.agent is not None:raise PolicyDenied('Only the customer can reject a proposed action')
    response=await data_api.execute(lambda:db.rpc('action_reject',{'p_command_id':str(command_id)}).execute(),'actions.reject')
    result=checked(response.data,ctx)
    if result.command.command_id!=command_id:raise ActionIntegrityError('Rejection returned another command')
    return result
