"""RM record reads and approved writes (F14), one Data API RPC each under the RM's own JWT.

The SQL functions (migration 0089) resolve the caller to an advisor from administrator-owned app_metadata plus the
rm.staff_logins link, check the client is in that advisor's current book, and audit every read and refusal. This module
adds a second, independent check (the verified RM session set by api/rm.py) so nothing here runs for a customer token.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date,datetime
from typing import Any
from uuid import UUID
from app.core import data_api
from app.core.observability import span
from app.schemas.contracts import RequestContext


@dataclass(frozen=True)
class RmSession:
    """Verified from the signed JWT: app_metadata.demobank_role=rm and the advisor id (never user_metadata)."""
    user_id: UUID
    advisor_id: UUID


RM_SESSION: ContextVar[RmSession|None]=ContextVar('rm_session',default=None)


class RmForbidden(PermissionError):
    """The caller is not a verified relationship manager."""


class RmDenied(PermissionError):
    """The client is not in the RM's current book; the refusal was audited in SQL."""


@contextmanager
def rm_scope(session: RmSession):
    token=RM_SESSION.set(session)
    try:yield session
    finally:RM_SESSION.reset(token)


def _session() -> RmSession:
    session=RM_SESSION.get()
    if session is None:raise RmForbidden('Relationship manager tools need a verified RM session')
    return session


async def rpc(name: str,params: dict[str,Any],ctx: RequestContext,tool: str) -> Any:
    _session()
    def run():
        with data_api.connect(ctx) as db:return db.rpc(name,params).execute().data
    with span(tool,'tool',tool=tool) as fields:
        data=await data_api.execute(run,tool)
        if isinstance(data,dict):
            fields['denied']=bool(data.get('denied'))
            if data.get('call_id'):fields['call_id']=str(data['call_id'])
    if isinstance(data,dict) and data.get('denied'):raise RmDenied('This client is not in your book.')
    return data


def _d(value: date|None) -> str|None:return value.isoformat() if value else None


# Reads ------------------------------------------------------------------------------------------------------------
async def profile(ctx):return await rpc('rm_profile',{},ctx,'rm.profile')
async def book(ctx,query: str|None=None):return await rpc('rm_book',{'p_query':query},ctx,'rm.book')
async def match_clients(ctx,text: str):return await rpc('rm_match_clients',{'p_text':text},ctx,'rm.match_clients')
async def client_profile(ctx,customer_id: int):return await rpc('rm_client_profile',{'p_customer_id':customer_id},ctx,'rm.client_profile')
async def meeting_notes(ctx,customer_id: int):return await rpc('rm_meeting_notes',{'p_customer_id':customer_id},ctx,'rm.meeting_notes')
async def shared_summary(ctx,customer_id: int):return await rpc('rm_shared_summary',{'p_customer_id':customer_id},ctx,'rm.shared_summary')
async def tickets(ctx):return await rpc('rm_tickets',{},ctx,'rm.tickets')
async def ticket(ctx,reference: str):return await rpc('rm_ticket',{'p_reference':reference},ctx,'rm.ticket')
async def review_status(ctx):return await rpc('rm_review_status',{},ctx,'rm.review_status')
async def appointments(ctx,start: date|None=None,end: date|None=None):
    return await rpc('rm_appointments',{'p_from':_d(start),'p_to':_d(end)},ctx,'rm.appointments')
async def availability(ctx):return await rpc('rm_availability',{},ctx,'rm.availability')
async def tasks(ctx,customer_id: int|None=None):return await rpc('rm_tasks',{'p_customer_id':customer_id},ctx,'rm.tasks')
async def followups(ctx):return await rpc('rm_followups',{},ctx,'rm.followups')
async def cash_events(ctx,customer_id: int):return await rpc('rm_cash_events',{'p_customer_id':customer_id},ctx,'rm.cash_events')
async def book_facts(ctx):return await rpc('rm_book_facts',{},ctx,'rm.book_facts')
async def handover_sources(ctx,customer_id: int):return await rpc('rm_handover_sources',{'p_customer_id':customer_id},ctx,'rm.handover_brief')
async def check_service_request(ctx,customer_id: int,category: str,summary: str,details: dict,urgency: str):
    return await rpc('rm_check_service_request',{'p_customer_id':customer_id,'p_category':category,'p_summary':summary,
        'p_details':details,'p_urgency':urgency},ctx,'rm.check_service_request')


# Approved writes ----------------------------------------------------------------------------------------------------
async def save_interaction(ctx,body):
    return await rpc('rm_save_interaction',{'p_request_key':str(body.request_key),'p_customer_id':body.customer_id,'p_met_on':body.met_on.isoformat(),
        'p_format':body.format,'p_title':body.title,'p_summary':body.summary,'p_commitments':body.commitments,'p_decisions':body.decisions,
        'p_client_requests':body.client_requests,'p_source_notes':body.source_notes,'p_structured':body.structured,'p_revisions':body.revisions},ctx,'rm.save_interaction')

async def create_tasks(ctx,body):
    return await rpc('rm_create_tasks',{'p_customer_id':body.customer_id,'p_note_id':str(body.note_id) if body.note_id else None,
        'p_tasks':[{**task.model_dump(mode='json'),'request_key':str(task.request_key)} for task in body.tasks]},ctx,'rm.create_tasks')

async def update_task(ctx,task_id: UUID,status: str):
    return await rpc('rm_update_task',{'p_task_id':str(task_id),'p_status':status},ctx,'rm.update_task')

async def create_service_request(ctx,body):
    return await rpc('rm_create_service_request',{'p_request_key':str(body.request_key),'p_customer_id':body.customer_id,'p_category':body.category,
        'p_summary':body.summary,'p_details':body.details,'p_urgency':body.urgency,'p_client_verified':body.client_verified,
        'p_note_id':str(body.note_id) if body.note_id else None},ctx,'rm.create_service_request')

async def send_client_update(ctx,body):
    return await rpc('rm_send_client_update',{'p_message_id':str(body.message_id),'p_customer_id':body.customer_id,'p_reference':body.reference,
        'p_body':body.body,'p_status':body.status,'p_draft_revision':body.draft_revision,'p_edited':body.edited},ctx,'rm.send_client_update')

async def record_failed_message(ctx,body,code: str):
    return await rpc('rm_record_failed_message',{'p_message_id':str(body.message_id),'p_customer_id':body.customer_id,'p_reference':body.reference,
        'p_failure_code':code},ctx,'rm.record_failed_message')

async def set_working_hours(ctx,weekday: int,starts: str|None,ends: str|None):
    return await rpc('rm_set_working_hours',{'p_weekday':weekday,'p_starts':starts,'p_ends':ends},ctx,'rm.set_working_hours')

async def add_block(ctx,starts: datetime,ends: datetime,reason: str):
    return await rpc('rm_add_block',{'p_starts':starts.isoformat(),'p_ends':ends.isoformat(),'p_reason':reason or None},ctx,'rm.add_block')

async def remove_block(ctx,block_id: UUID):return await rpc('rm_remove_block',{'p_block_id':str(block_id)},ctx,'rm.remove_block')
async def conversations(ctx):return await rpc('rm_conversations',{},ctx,'rm.conversations')
async def conversation(ctx,conversation_id: UUID):return await rpc('rm_conversation',{'p_conversation_id':str(conversation_id)},ctx,'rm.conversation')
async def save_conversation(ctx,conversation_id: UUID,title: str,messages: list,context: dict):
    return await rpc('rm_save_conversation',{'p_conversation_id':str(conversation_id),'p_title':title,'p_messages':messages,'p_context':context},ctx,'rm.save_conversation')
async def delete_conversation(ctx,conversation_id: UUID):return await rpc('rm_delete_conversation',{'p_conversation_id':str(conversation_id)},ctx,'rm.delete_conversation')
async def dismiss_followup(ctx,key: str):return await rpc('rm_dismiss_followup',{'p_item_key':key},ctx,'rm.dismiss_followup')
