"""Permission-first customer reads using the caller's JWT, invoker RLS and explicit SQL customer predicates.

Each bounded snapshot and its data.read audit commit in one RPC transaction. The returned
customer scope, required payload fields and server call_id/as_of are validated before release.
"""
from datetime import date
from typing import Annotated,TypeVar
from pydantic import Field,JsonValue,TypeAdapter
from app.core import data_api
from app.core.observability import span
from app.schemas.contracts import RequestContext,ToolResult
from app.schemas.customer_data import (BankAccount,ComplianceCase,CreditCard,CustomerEnvelope,CustomerLoan,
    CustomerProfile,CustomerTransaction,RelationshipAssignment,TransactionQuery)
from app.schemas.relationship import AvailableSlot,RelationshipRequest,SlotsQuery
from app.schemas.appointments import AppointmentRecord
from app.schemas.consent_actions import ConsentRegistry
from app.services.authorization import require_permission

STUB=False
T=TypeVar('T')
PROFILE=TypeAdapter(CustomerEnvelope[CustomerProfile])
ACCOUNTS=TypeAdapter(CustomerEnvelope[Annotated[list[BankAccount],Field(max_length=200)]])
CARDS=TypeAdapter(CustomerEnvelope[Annotated[list[CreditCard],Field(max_length=200)]])
LOANS=TypeAdapter(CustomerEnvelope[Annotated[list[CustomerLoan],Field(max_length=200)]])
TRANSACTIONS=TypeAdapter(CustomerEnvelope[Annotated[list[CustomerTransaction],Field(max_length=200)]])
CASE=TypeAdapter(CustomerEnvelope[ComplianceCase | None])
ASSIGNMENT=TypeAdapter(CustomerEnvelope[RelationshipAssignment | None])
REQUESTS=TypeAdapter(CustomerEnvelope[Annotated[list[RelationshipRequest],Field(max_length=200)]])
SLOTS=TypeAdapter(CustomerEnvelope[Annotated[list[AvailableSlot],Field(max_length=20)]])
APPOINTMENTS=TypeAdapter(CustomerEnvelope[Annotated[list[AppointmentRecord],Field(max_length=200)]])
CONSENTS=TypeAdapter(CustomerEnvelope[ConsentRegistry])


class CustomerDataIntegrityError(ValueError):
    """Returned records do not match their authenticated customer scope or requested bound."""


def _snapshot(rpc: str,params: dict[str,JsonValue],adapter: TypeAdapter[CustomerEnvelope[T]],ctx: RequestContext) -> CustomerEnvelope[T]:
    with data_api.connect(ctx) as db:
        envelope=adapter.validate_python(db.rpc(rpc,{'p_customer_id':ctx.principal.customer_id,**params}).execute().data)
    if envelope.customer_id!=ctx.principal.customer_id:
        raise CustomerDataIntegrityError('Customer snapshot does not match the authenticated request scope')
    return envelope


async def _read(tool: str,rpc: str,params: dict[str,JsonValue],adapter: TypeAdapter[CustomerEnvelope[T]],ctx: RequestContext) -> CustomerEnvelope[T]:
    require_permission(ctx.principal,tool,{'customer_id':ctx.principal.customer_id},ctx)
    with span(tool,'tool',tool=tool) as fields:
        envelope=await data_api.execute(lambda:_snapshot(rpc,params,adapter,ctx),tool)
        fields['call_id']=str(envelope.call_id)
        return envelope


def _result(envelope: CustomerEnvelope[T]) -> ToolResult:
    return ToolResult(data=envelope.model_dump(mode='json')['data'],call_id=envelope.call_id,as_of=envelope.as_of)


async def get_profile(ctx: RequestContext) -> ToolResult:
    return _result(await _read('customer.get_profile','customer_read_profile',{},PROFILE,ctx))


async def get_accounts(ctx: RequestContext) -> ToolResult:
    return _result(await _read('customer.get_accounts','customer_read_accounts',{},ACCOUNTS,ctx))


async def get_transactions(ctx: RequestContext,from_: date | None,to: date | None,filters: dict[str,JsonValue] | None) -> ToolResult:
    query=TransactionQuery.model_validate({'from_date':from_,'to_date':to,'filters':filters})
    params: dict[str,JsonValue]={'p_from':query.from_date.isoformat() if query.from_date else None,
        'p_to':query.to_date.isoformat() if query.to_date else None,
        'p_filters':query.filters.model_dump(mode='json',exclude_none=True) if query.filters else {}}
    return _result(await _read('customer.get_transactions','customer_read_transactions',params,TRANSACTIONS,ctx))


async def get_credit_cards(ctx: RequestContext) -> ToolResult:
    return _result(await _read('customer.get_credit_cards','customer_read_cards',{},CARDS,ctx))


async def get_loans(ctx: RequestContext) -> ToolResult:
    return _result(await _read('customer.get_loans','customer_read_loans',{},LOANS,ctx))


async def get_compliance_case(ctx: RequestContext) -> ToolResult:
    """Latest client-visible case; internal review records are never queried."""
    return _result(await _read('compliance.get_case','customer_read_compliance',{},CASE,ctx))


async def get_rm_assignment(ctx: RequestContext) -> ToolResult:
    """Current and most recent previous relationship manager, restricted by assignment RLS."""
    return _result(await _read('rm.get_assignment','customer_read_assignment',{},ASSIGNMENT,ctx))


async def get_requests(ctx: RequestContext) -> ToolResult:
    envelope=await _read('requests.list','customer_read_requests',{},REQUESTS,ctx)
    if any(row.customer_id!=envelope.customer_id for row in envelope.data):
        raise CustomerDataIntegrityError('Relationship requests contain records outside the authenticated customer scope')
    return ToolResult(data=[row.model_dump(mode='json',exclude={'customer_id'}) for row in envelope.data],
        call_id=envelope.call_id,as_of=envelope.as_of)


async def get_appointments(ctx: RequestContext) -> ToolResult:
    return _result(await _read('appointments.list','customer_read_appointments',{},APPOINTMENTS,ctx))


async def get_consent_records(ctx: RequestContext) -> ToolResult:
    return _result(await _read('consent.records','customer_read_consent_records',{},CONSENTS,ctx))


async def get_free_slots(query: SlotsQuery,ctx: RequestContext) -> ToolResult:
    """Suggestions only; booking must recheck conflicts atomically."""
    envelope=await _read('appointments.find_slots','customer_read_slots',{'p_from':query.from_date.isoformat(),
        'p_to':query.to_date.isoformat(),'p_limit':query.limit},SLOTS,ctx)
    if len(envelope.data)>query.limit or any(row.customer_id!=envelope.customer_id for row in envelope.data):
        raise CustomerDataIntegrityError('Appointment suggestions exceed their requested limit or customer scope')
    return ToolResult(data=[row.model_dump(mode='json',exclude={'customer_id'}) for row in envelope.data],
        call_id=envelope.call_id,as_of=envelope.as_of)
