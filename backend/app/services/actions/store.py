"""Atomic, RLS-scoped ledger reads. SQL reads commands and receipts in one statement snapshot."""
from uuid import UUID
from postgrest import SyncPostgrestClient
from pydantic import TypeAdapter
from app.core.data_api import execute
from app.schemas.actions import ActionOutcome
from app.schemas.contracts import RequestContext
from app.services.authorization import require_permission


class ActionIntegrityError(ValueError):
    """The returned action or receipt does not match the requested identity and scope."""


class ActionNotFound(LookupError):
    """The owned action was not returned by the user's RLS-scoped read."""


def checked(value: object,ctx: RequestContext) -> ActionOutcome:
    result=ActionOutcome.model_validate(value)
    if result.command.customer_id!=ctx.principal.customer_id:raise ActionIntegrityError('Action was returned outside the customer scope')
    return result


async def load(command_id: UUID,ctx: RequestContext,db: SyncPostgrestClient) -> ActionOutcome:
    require_permission(ctx.principal,'actions.read',{'customer_id':ctx.principal.customer_id},ctx)
    response=await execute(lambda:db.rpc('action_read_customer',{'p_command_id':str(command_id),
        'p_customer_id':ctx.principal.customer_id}).execute(),'actions.read')
    if response.data is None:raise ActionNotFound('Command was not found in this customer scope')
    result=checked(response.data,ctx)
    if result.command.command_id!=command_id:raise ActionIntegrityError('Action identity changed')
    return result


async def recent(ctx: RequestContext,db: SyncPostgrestClient) -> list[ActionOutcome]:
    require_permission(ctx.principal,'actions.list',{'customer_id':ctx.principal.customer_id},ctx)
    response=await execute(lambda:db.rpc('action_list_customer',{'p_customer_id':ctx.principal.customer_id}).execute(),'actions.list')
    records=TypeAdapter(list[ActionOutcome]).validate_python(response.data)
    if len(records)>50 or len({item.command.command_id for item in records})!=len(records):
        raise ActionIntegrityError('Action listing exceeded its bound or repeated a command')
    return [checked(item,ctx) for item in records]
