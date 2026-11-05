"""Meeting connectors: caller-JWT Data API for the UI; restricted SQL login for the worker.

The worker URL must name its provisioned LOGIN role, a member of demobank_meetings, never an owner/service login.
No database credentials or caller tokens are placed in graph state or model messages.
"""
import json
import logging
from app.core.observability import log_event
import time
from collections.abc import Mapping
from typing import TypeAlias
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from postgrest.exceptions import APIError
from pydantic import JsonValue, SecretStr, TypeAdapter
from app.core import data_api
from app.schemas.contracts import RequestContext
from app.schemas.meeting_lifecycle import Claim, Claims, JobContext, MinutesContent

Parameter: TypeAlias = str | int | bool | UUID | list[UUID] | Jsonb | None
PUBLIC=frozenset({'meeting_start_workflow','meeting_document_call_start','meeting_document_call_end','meeting_submit_notes',
 'meeting_notes_context','meeting_save_notes_draft','meeting_approve_minutes','meeting_create_followups','meeting_share_recap',
 'meeting_complete_followup','meeting_view','meeting_generation_receipt','meeting_save_generated_draft','meeting_import_transcript',
 'meeting_acknowledge_notice','meeting_transcript_revoke','meeting_identity','meeting_slots','meeting_rm_propose','meeting_appointment',
 'meeting_zoom_reserve','meeting_link','meeting_link_save','meeting_record_refusal'})

class MeetingStoreError(RuntimeError):
    def __init__(self,code: str,message: str) -> None:
        self.code=code
        super().__init__(message)


def serializable(value: object) -> JsonValue:
    if isinstance(value,UUID):return str(value)
    if isinstance(value,Jsonb):return TypeAdapter(JsonValue).validate_python(value.obj)
    raise TypeError('Unsupported meeting RPC parameter: '+type(value).__name__)


class ApplicationDatabase:
    def __init__(self,ctx: RequestContext) -> None:
        self.ctx=ctx

    def call(self,name: str,parameters: Mapping[str,Parameter],claims: Claims) -> JsonValue:
        if name not in PUBLIC:raise ValueError('Function is outside the meeting API')
        if claims.sub!=self.ctx.principal.user_id:raise PermissionError('Meeting caller does not match the gateway session')
        wire=TypeAdapter(dict[str,JsonValue]).validate_python(json.loads(json.dumps(dict(parameters),default=serializable)))
        for attempt in range(3):
            try:
                with data_api.connect(self.ctx) as db:return TypeAdapter(JsonValue).validate_python(db.rpc(name,wire).execute().data)
            except APIError as error:
                code=data_api.database_code(error)
                if attempt==2 or code not in data_api.TRANSIENT_CODES:
                    raise MeetingStoreError(code or 'database_unavailable',error.message) from error
                log_event('meeting_rpc_retry',level=logging.WARNING,operation=name,code=code,attempt=attempt+1)
                time.sleep(0.5*2**attempt)
            except data_api.NETWORK_ERRORS:
                if attempt==2:raise
                log_event('meeting_rpc_network_retry',level=logging.WARNING,operation=name,attempt=attempt+1)
                time.sleep(0.5*2**attempt)
        raise RuntimeError('Meeting RPC retries ended without a result')

    def record_refusal(self,operation: str,code: str,claims: Claims) -> None:
        self.call('meeting_record_refusal',{'p_operation':operation,'p_code':code},claims)


class WorkerDatabase:
    def __init__(self,uri: SecretStr) -> None:
        self.uri=uri

    def claim(self) -> Claim | None:
        with psycopg.connect(self.uri.get_secret_value(),row_factory=dict_row) as db:
            db.execute('set local role demobank_meetings')
            row=db.execute('select * from meetings.claim_next()').fetchone()
            if row is None:raise RuntimeError('Meeting queue returned no response row')
            return None if row['event_id'] is None else Claim.model_validate(row)

    def context(self,claim: Claim) -> JobContext:
        with psycopg.connect(self.uri.get_secret_value()) as db:
            db.execute('set local role demobank_meetings')
            row=db.execute('select meetings.job_context(%s,%s)',(claim.event_id,claim.claim_token)).fetchone()
            if row is None:raise RuntimeError('Meeting job context returned no row')
            return JobContext.model_validate(row[0])

    def save(self,claim: Claim,context: JobContext,content: MinutesContent) -> JsonValue:
        with psycopg.connect(self.uri.get_secret_value()) as db:
            db.execute('set local role demobank_meetings')
            row=db.execute('select meetings.save_job_draft(%s,%s,%s,%s,%s,%s)',(claim.event_id,claim.claim_token,context.source_hash,
                context.revision,context.consent_epoch,Jsonb(content.model_dump(mode='json')))).fetchone()
            if row is None:raise RuntimeError('Meeting draft save returned no row')
            return TypeAdapter(JsonValue).validate_python(row[0])

    def finish(self,claim: Claim) -> None:
        with psycopg.connect(self.uri.get_secret_value()) as db:
            db.execute('set local role demobank_meetings');db.execute('select meetings.finish(%s,%s,true,null)',(claim.event_id,claim.claim_token))

    def fail(self,claim: Claim,error_code: str) -> None:
        with psycopg.connect(self.uri.get_secret_value()) as db:
            db.execute('set local role demobank_meetings');db.execute('select meetings.finish(%s,%s,false,%s)',(claim.event_id,claim.claim_token,error_code))

    def reject(self,claim: Claim,error_code: str) -> None:
        with psycopg.connect(self.uri.get_secret_value()) as db:
            db.execute('set local role demobank_meetings');db.execute('select meetings.reject(%s,%s,%s)',(claim.event_id,claim.claim_token,error_code))

    def purge_transcripts(self) -> int:
        with psycopg.connect(self.uri.get_secret_value()) as db:
            db.execute('set local role demobank_meetings');row=db.execute('select meetings.purge_transcripts()').fetchone()
            if row is None:raise RuntimeError('Transcript retention returned no response row')
            return int(row[0])

    def verify_identity(self) -> None:
        with psycopg.connect(self.uri.get_secret_value()) as db:
            row=db.execute("select rolsuper,rolcreaterole,rolcreatedb,pg_has_role(current_user,'demobank_meetings','MEMBER') from pg_roles where rolname=session_user").fetchone()
            if row is None or any(row[:3]) or not row[3]:raise PermissionError('Meeting worker requires a restricted non-owner LOGIN in demobank_meetings')

    def apply_lifecycle(self,claim: Claim) -> None:
        with psycopg.connect(self.uri.get_secret_value()) as db:
            db.execute('set local role demobank_meetings')
            db.execute('select meetings.apply_lifecycle(%s,%s)',(claim.event_id,claim.claim_token))
