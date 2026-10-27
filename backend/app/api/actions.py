"""Customer command review. Confirmation accepts only the command id; targets come from owned storage."""
import json
from collections.abc import Awaitable,Callable
from typing import TypeVar
from uuid import UUID
import httpx
from fastapi import APIRouter,Depends,Request
from postgrest.exceptions import APIError
from pydantic import ValidationError
from app.api.audited import flush
from app.core import data_api
from app.core.errors import ApiError
from app.core.gateway import get_ctx
from app.core.observability import log_event
from app.schemas.actions import ActionCase,ActionOutcome,CreateAction,CreateActionCase
from app.schemas.contracts import ActionCommand,RequestContext
from app.schemas.disputes import DisputeInput
from app.schemas.events import WorkflowPreview,WorkflowStatus
from app.services import actions,audit,customer_data,workflows
from app.services.checkpoint_codec import CheckpointIntegrityError
from app.services.conversations import ConversationIntegrityError
from app.services.actions import store
from app.services.authorization import PolicyDenied,require_permission

router=APIRouter()
T=TypeVar('T')


async def response_for(operation: Callable[[],Awaitable[T]],ctx: RequestContext,request: Request) -> T:
    if ctx.principal.kind!='customer':raise ApiError(401,'auth_required','Please sign in to review actions.')
    if ctx.principal.customer_id is None:raise ApiError(403,'profile_missing','A linked demo customer profile is required for actions.')
    batch=audit.start_batch(request.state.db,session_id=ctx.session_id)
    try:result=await operation()
    except ApiError:
        await flush(batch)
        raise
    except (PolicyDenied,store.ActionNotFound,actions.ActionStateError,store.ActionIntegrityError,ConversationIntegrityError,CheckpointIntegrityError,customer_data.CustomerDataIntegrityError,ValidationError,APIError,httpx.TransportError) as error:
        code=data_api.database_code(error) if isinstance(error,APIError) else None
        log_event('action_request_failed',db_code=code,error_type=type(error).__name__)
        await flush(batch)
        if isinstance(error,store.ActionNotFound) or code=='P0002':raise ApiError(404,'action_not_found','This command was not found in your demo profile.') from error
        if isinstance(error,PolicyDenied) or code=='42501':raise ApiError(403,'action_forbidden','This session cannot perform that action. Reopen your current demo profile.') from error
        if isinstance(error,actions.ActionStateError):raise ApiError(409,'action_'+str(error),'This proposal is '+str(error)+'. No new action was executed.') from error
        if code=='23505':raise ApiError(409,'action_conflict','This command id or idempotency key already names another action. Reload the command.') from error
        if code=='22023':raise ApiError(422,'action_invalid','The action parameters are invalid for this demo service.') from error
        raise ApiError(503,'action_unavailable','The action result cannot be verified. Reload its recorded status before retrying.') from error
    await flush(batch)
    return result


@router.get('/commands',response_model=list[ActionOutcome])
async def recent(request: Request,ctx: RequestContext=Depends(get_ctx)) -> list[ActionOutcome]:
    async def operation() -> list[ActionOutcome]:
        with data_api.connect(ctx) as db:return await store.recent(ctx,db)
    return await response_for(operation,ctx,request)


@router.post('/actions/cases',response_model=ActionCase)
async def create_case(query: CreateActionCase,request: Request,ctx: RequestContext=Depends(get_ctx)) -> ActionCase:
    async def operation() -> ActionCase:
        require_permission(ctx.principal,'actions.create_case',{'customer_id':ctx.principal.customer_id},ctx)
        with data_api.connect(ctx) as db:
            response=await data_api.execute(lambda:db.rpc('action_create_case',{'p_session_id':str(query.session_id),'p_case_id':str(query.case_id)}).execute(),'actions.create_case')
        result=ActionCase.model_validate(response.data)
        if result.case_id!=query.case_id or result.session_id!=query.session_id or result.customer_id!=ctx.principal.customer_id:
            raise store.ActionIntegrityError('The returned action case does not match the customer, case or session')
        return result
    return await response_for(operation,ctx,request)


@router.get('/commands/{command_id}',response_model=ActionOutcome)
async def load(command_id: UUID,request: Request,ctx: RequestContext=Depends(get_ctx)) -> ActionOutcome:
    async def operation() -> ActionOutcome:
        with data_api.connect(ctx) as db:return await store.load(command_id,ctx,db)
    return await response_for(operation,ctx,request)


@router.post('/commands',response_model=ActionOutcome)
async def propose(query: CreateAction,request: Request,ctx: RequestContext=Depends(get_ctx)) -> ActionOutcome:
    async def operation() -> ActionOutcome:
        command=ActionCommand(command_id=query.command_id,type=query.type,target=query.target.model_dump(mode='json'),
            idempotency_key=f'{query.case_id}:{query.task_id}:{query.type}')
        result=await actions.run_action(command,ctx.model_copy(update={'case_id':query.case_id}),False)
        with data_api.connect(ctx) as db:return await store.load(result.command_id,ctx,db)
    return await response_for(operation,ctx,request)


async def confirmation_answers(request: Request,command_type: str) -> DisputeInput | None:
    """Only a dispute takes answers ({"answers": {...}}); any other confirmation body stays ignored, as before."""
    if command_type!='card.dispute':return None
    try:body=json.loads(await request.body() or b'null')
    except (json.JSONDecodeError,UnicodeDecodeError) as error:
        raise ApiError(422,'action_invalid','Answer whether you still have the card to confirm this dispute draft.') from error
    answers=body.get('answers') if isinstance(body,dict) else None
    try:return DisputeInput.model_validate(answers)
    except ValidationError as error:
        raise ApiError(422,'action_answers_required','Answer whether you still have the card to confirm this dispute draft.') from error


@router.post('/commands/{command_id}/confirm',response_model=ActionOutcome)
async def confirm(command_id: UUID,request: Request,ctx: RequestContext=Depends(get_ctx)) -> ActionOutcome:
    async def operation() -> ActionOutcome:
        with data_api.connect(ctx) as db:current=await store.load(command_id,ctx,db)
        item=current.command
        command=ActionCommand(command_id=item.command_id,type=item.type,idempotency_key=item.idempotency_key,target=item.target.model_dump(mode='json'))
        answers=await confirmation_answers(request,item.type)
        try:result=await actions.run_action(command,ctx.model_copy(update={'case_id':item.case_id}),True,confirmation_input=answers)
        except actions.ActionStateError:
            if current.workflow is not None:await workflows.resume(current.workflow.turn_id,ctx)
            raise
        if current.workflow is not None:await workflows.resume(current.workflow.turn_id,ctx)
        with data_api.connect(ctx) as db:return await store.load(result.command_id,ctx,db)
    return await response_for(operation,ctx,request)


@router.post('/commands/{command_id}/reject',response_model=ActionOutcome)
async def reject(command_id: UUID,request: Request,ctx: RequestContext=Depends(get_ctx)) -> ActionOutcome:
    async def operation() -> ActionOutcome:
        with data_api.connect(ctx) as db:result=await actions.reject_action(command_id,ctx,db)
        if result.workflow is not None:await workflows.resume(result.workflow.turn_id,ctx)
        with data_api.connect(ctx) as db:return await store.load(command_id,ctx,db)
    return await response_for(operation,ctx,request)


@router.post('/workflows/{turn_id}/resume',response_model=WorkflowStatus)
async def resume(turn_id: UUID,request: Request,ctx: RequestContext=Depends(get_ctx)) -> WorkflowStatus:
    return await response_for(lambda:workflows.resume(turn_id,ctx),ctx,request)


@router.get('/workflows/{turn_id}/preview',response_model=WorkflowPreview)
async def preview(turn_id: UUID,request: Request,ctx: RequestContext=Depends(get_ctx)) -> WorkflowPreview:
    return await response_for(lambda:workflows.preview(turn_id,ctx),ctx,request)
