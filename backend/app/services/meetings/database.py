"""Meeting connectors: caller-JWT Data API for the UI; restricted SQL login for the worker.

The meeting agents' reads (tools.MEETING_READS) are MCP tools/calls through the in-process server (agent_read); their
drafts (agent_call) and every worker call stay direct, so the worker's private login never reaches MCP.
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
from app.schemas.contracts import Principal, RequestContext
from app.schemas.meeting_agents import MeetingAgent, MeetingRmAccess, MeetingWorkerAccess
from app.schemas.meeting_lifecycle import Claim, Claims, JobContext, MinutesContent
from app.schemas.zoom_capture import DiscoveryPermit,FetchPermit,FetchedEvidence
from app.schemas.meeting_chunks import ChunkSlot
from app.schemas.meeting_reviews import GraphRun
from app.schemas.zoom_scheduling import Cancellation,ProviderMeeting,ScheduledMeeting
from app.services.agents.registry import MINUTES_WRITER
from app.services.meetings.zoom import HostedMeeting,Owner
from app.services.authorization import PolicyDenied
from app.services.tools import MEETING_READS,meeting_call,meeting_read

Parameter: TypeAlias = str | int | bool | UUID | list[UUID] | Jsonb | None
PUBLIC=frozenset({'meeting_start_workflow','meeting_document_call_start','meeting_document_call_end','meeting_submit_notes',
 'meeting_save_notes_draft','meeting_approve_minutes','meeting_create_followups','meeting_share_recap',
 'meeting_complete_followup','meeting_view','meeting_generation_receipt','meeting_save_generated_draft','meeting_import_transcript',
 'meeting_acknowledge_notice','meeting_transcript_revoke','meeting_identity','meeting_slots','meeting_rm_propose','meeting_appointment',
 'meeting_zoom_reserve','meeting_link','meeting_link_save','meeting_record_refusal','meeting_zoom_link_bind','meeting_zoom_evidence','meeting_zoom_map_speakers',
 'meeting_correct_call_times','meeting_reconcile_zoom_times','meeting_retry_job','meeting_recovery',
 'meeting_current_brief','meeting_decide_brief',
 'meeting_approve_pack','meeting_share_pack','meeting_current_pack',
 'meeting_review_minutes','meeting_regenerate_minutes','meeting_minutes_review','meeting_lifecycle_status','meeting_recap_document',
 'meeting_reminders','meeting_acknowledge_reminder','meeting_request_discovery','meeting_request_zoom',
 'meeting_transcript_standing','meeting_set_transcript_standing'})

class MeetingStoreError(RuntimeError):
    def __init__(self,code: str,message: str) -> None:
        self.code=code
        super().__init__(message)


def serializable(value: object) -> JsonValue:
    if isinstance(value,UUID):return str(value)
    if isinstance(value,Jsonb):return TypeAdapter(JsonValue).validate_python(value.obj)
    raise TypeError('Unsupported meeting RPC parameter: '+type(value).__name__)


def wire(parameters: Mapping[str,Parameter]) -> dict[str,JsonValue]:
    return TypeAdapter(dict[str,JsonValue]).validate_python(json.loads(json.dumps(dict(parameters),default=serializable)))


def require_runtime_login(uri: SecretStr,group: str) -> None:
    """Refuse owners, administration flags and any membership outside the one required runtime group."""
    if group not in ('demobank_meetings','demobank_meeting_ingress'):raise ValueError('Unknown meeting runtime group')
    with psycopg.connect(uri.get_secret_value(),connect_timeout=5) as db:
        row=db.execute("""select r.rolsuper,r.rolcreaterole,r.rolcreatedb,r.rolbypassrls,r.rolreplication,
            pg_has_role(session_user,%s,'MEMBER'),
            exists(select 1 from pg_roles other where other.oid<>r.oid and other.rolname<>%s and pg_has_role(r.oid,other.oid,'MEMBER')),
            exists(select 1 from pg_database d where d.datdba=r.oid),
            exists(select 1 from pg_namespace n where n.nspowner=r.oid and n.nspname not like 'pg_temp_%%' and n.nspname not like 'pg_toast_temp_%%')
            from pg_roles r where r.rolname=session_user""",(group,group)).fetchone()
        if row is None or any(row[:5]) or not row[5] or any(row[6:]):
            raise PermissionError('Meeting runtime requires a non-owner LOGIN with only '+group+' membership and no administration/BYPASSRLS privileges')


class ApplicationDatabase:
    def __init__(self,ctx: RequestContext) -> None:
        self.ctx=ctx

    def call(self,name: str,parameters: Mapping[str,Parameter],claims: Claims) -> JsonValue:
        if name not in PUBLIC:raise ValueError('Function is outside the meeting API')
        if claims.sub!=self.ctx.principal.user_id:raise PermissionError('Meeting caller does not match the gateway session')
        return self._execute(name,parameters)

    async def agent_read(self,agent: MeetingAgent,name: str,meeting: UUID,parameters: Mapping[str,Parameter],claims: Claims) -> JsonValue:
        """A meeting agent's read as an MCP tools/call (tools.meeting_read): the tool checks the meeting policy, then reads
        with the caller's JWT (read below). A refusal is a 42501 MeetingStoreError, as for agent_call."""
        try:return await meeting_read(name,wire(parameters),self.ctx,MeetingRmAccess(agent,self.ctx.principal,meeting,claims))
        except PolicyDenied as error:
            raise MeetingStoreError('42501',str(error)) from error

    def read(self,name: str,parameters: Mapping[str,Parameter]) -> JsonValue:
        """The SQL reader behind a meeting read tool, run by the tool after the meeting policy; never a UI call."""
        if name not in MEETING_READS:raise ValueError('Function is not a meeting agent read')
        return self._execute(name,parameters)

    def agent_call(self,agent: MeetingAgent,name: str,meeting: UUID,parameters: Mapping[str,Parameter],claims: Claims) -> JsonValue:
        """A meeting agent's draft write, gated and traced off MCP (meeting_call); its reads go through agent_read."""
        if name in MEETING_READS:raise ValueError('Meeting agent reads go through agent_read (MCP)')
        access=MeetingRmAccess(agent,self.ctx.principal,meeting,claims)
        target=parameters.get('p_meeting_id')
        try:
            with meeting_call(name,target if isinstance(target,UUID) else None,access) as facts:
                try:return self._execute(name,parameters)
                except MeetingStoreError as error:
                    if error.code=='42501':facts.update(allowed=False,reason='meeting_scope_denied')
                    raise
        except PolicyDenied as error:
            raise MeetingStoreError('42501',str(error)) from error

    def _execute(self,name: str,parameters: Mapping[str,Parameter]) -> JsonValue:
        body=wire(parameters)
        for attempt in range(3):
            try:
                with data_api.connect(self.ctx) as db:return TypeAdapter(JsonValue).validate_python(db.rpc(name,body).execute().data)
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

    def schedule_due(self) -> int:
        with psycopg.connect(self.uri.get_secret_value()) as db:
            db.execute('set local role demobank_meetings');row=db.execute('select meetings.schedule_due()').fetchone()
            if row is None:raise RuntimeError('Meeting timer scheduler returned no response')
            return int(row[0])

    def schedule_graph_updates(self) -> int:
        with psycopg.connect(self.uri.get_secret_value()) as db:
            db.execute('set local role demobank_meetings');row=db.execute('select meetings.schedule_graph_updates()').fetchone()
            if row is None:raise RuntimeError('Meeting graph scheduler returned no response')
            return int(row[0])

    def graph_open(self,claim: Claim,run_id: UUID) -> GraphRun:
        with psycopg.connect(self.uri.get_secret_value()) as db:
            db.execute('set local role demobank_meetings');row=db.execute('select meetings.graph_open(%s,%s,%s)',(claim.event_id,claim.claim_token,run_id)).fetchone()
            if row is None:raise RuntimeError('Meeting graph did not acquire a fenced run')
            result=GraphRun.model_validate(row[0])
            if result.meeting_id!=claim.meeting_id or result.event_id!=claim.event_id or result.run_id!=run_id:
                raise PermissionError('Graph run does not match the claimed meeting event')
            return result

    def graph_close(self,meeting: UUID,run_id: UUID) -> None:
        with psycopg.connect(self.uri.get_secret_value()) as db:
            db.execute('set local role demobank_meetings');db.execute('select meetings.graph_close(%s,%s)',(meeting,run_id))

    def graph_status(self,meeting: UUID,run_id: UUID) -> JsonValue:
        with psycopg.connect(self.uri.get_secret_value()) as db:
            db.execute('set local role demobank_meetings');row=db.execute('select meetings.graph_status(%s,%s)',(meeting,run_id)).fetchone()
            if row is None:raise RuntimeError('Meeting graph returned no lifecycle state')
            return TypeAdapter(JsonValue).validate_python(row[0])

    def graph_read(self,meeting: UUID,run_id: UUID,namespace: str,checkpoint_id: str|None) -> JsonValue:
        with psycopg.connect(self.uri.get_secret_value()) as db:
            db.execute('set local role demobank_meetings');row=db.execute('select meetings.graph_read(%s,%s,%s,%s)',(meeting,run_id,namespace,checkpoint_id)).fetchone()
            if row is None:raise RuntimeError('Meeting checkpoint reader returned no response')
            return TypeAdapter(JsonValue).validate_python(row[0])

    def graph_save(self,meeting: UUID,run_id: UUID,namespace: str,checkpoint_id: str,data: JsonValue) -> None:
        with psycopg.connect(self.uri.get_secret_value()) as db:
            db.execute('set local role demobank_meetings');db.execute('select meetings.graph_save(%s,%s,%s,%s,%s)',(meeting,run_id,namespace,checkpoint_id,Jsonb(data)))

    def graph_save_writes(self,meeting: UUID,run_id: UUID,namespace: str,checkpoint_id: str,writes: JsonValue) -> None:
        with psycopg.connect(self.uri.get_secret_value()) as db:
            db.execute('set local role demobank_meetings');db.execute('select meetings.graph_save_writes(%s,%s,%s,%s,%s)',(meeting,run_id,namespace,checkpoint_id,Jsonb(writes)))

    def discovery_permit(self,claim: Claim) -> DiscoveryPermit:
        with psycopg.connect(self.uri.get_secret_value()) as db:
            db.execute('set local role demobank_meetings')
            row=db.execute('select meetings.discovery_permit(%s,%s)',(claim.event_id,claim.claim_token)).fetchone()
            if row is None:raise RuntimeError('Transcript discovery returned no permit')
            return DiscoveryPermit.model_validate(row[0])

    def zoom_schedule_context(self,claim: Claim) -> ScheduledMeeting|None:
        with psycopg.connect(self.uri.get_secret_value()) as db:
            db.execute('set local role demobank_meetings');row=db.execute('select meetings.zoom_schedule_context(%s,%s)',(claim.event_id,claim.claim_token)).fetchone()
            if row is None:raise RuntimeError('Zoom scheduling context returned no response')
            return None if row[0] is None else ScheduledMeeting.model_validate(row[0])

    def reserve_zoom_job(self,claim: Claim) -> bool:
        with psycopg.connect(self.uri.get_secret_value()) as db:
            db.execute('set local role demobank_meetings');row=db.execute('select meetings.reserve_zoom_job(%s,%s)',(claim.event_id,claim.claim_token)).fetchone()
            if row is None or type(row[0]) is not bool:raise RuntimeError('Zoom reservation returned no boolean outcome')
            return row[0]

    def release_refused_zoom(self,claim: Claim) -> None:
        with psycopg.connect(self.uri.get_secret_value()) as db:
            db.execute('set local role demobank_meetings');db.execute('select meetings.release_refused_zoom(%s,%s)',(claim.event_id,claim.claim_token))

    def commit_scheduled_zoom(self,claim: Claim,remote: HostedMeeting,owner: Owner) -> None:
        with psycopg.connect(self.uri.get_secret_value()) as db:
            db.execute('set local role demobank_meetings')
            db.execute('select meetings.commit_scheduled_zoom(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (claim.event_id,claim.claim_token,remote.id,remote.join_url,remote.start_time,remote.duration,owner.account_id,owner.id,remote.uuid))

    def zoom_cancel_context(self,claim: Claim) -> ProviderMeeting|None:
        with psycopg.connect(self.uri.get_secret_value()) as db:
            db.execute('set local role demobank_meetings');row=db.execute('select meetings.zoom_cancel_context(%s,%s)',(claim.event_id,claim.claim_token)).fetchone()
            if row is None:raise RuntimeError('Zoom cancellation context returned no response')
            return None if row[0] is None else ProviderMeeting.model_validate(row[0])

    def commit_zoom_cancellation(self,claim: Claim,provider_id: int,outcome: Cancellation) -> None:
        with psycopg.connect(self.uri.get_secret_value()) as db:
            db.execute('set local role demobank_meetings');db.execute('select meetings.commit_zoom_cancellation(%s,%s,%s,%s)',(claim.event_id,claim.claim_token,provider_id,outcome))

    def queue_discovered_note(self,claim: Claim,permit: DiscoveryPermit,note_id: str) -> int:
        with psycopg.connect(self.uri.get_secret_value()) as db:
            db.execute('set local role demobank_meetings')
            row=db.execute('select meetings.queue_discovered_note(%s,%s,%s,%s)',(claim.event_id,claim.claim_token,permit.consent_epoch,note_id)).fetchone()
            if row is None:raise RuntimeError('Discovered note was not durably queued')
            return int(row[0])

    def report_missing_transcript(self,claim: Claim) -> None:
        with psycopg.connect(self.uri.get_secret_value()) as db:
            db.execute('set local role demobank_meetings');db.execute('select meetings.report_missing_transcript(%s,%s)',(claim.event_id,claim.claim_token))

    def checkpoint(self,claim: Claim) -> JsonValue:
        with psycopg.connect(self.uri.get_secret_value()) as db:
            db.execute('set local role demobank_meetings')
            row=db.execute('select meetings.checkpoint_claim(%s,%s)',(claim.event_id,claim.claim_token)).fetchone()
            if row is None:raise RuntimeError('Lifecycle checkpoint returned no response')
            return TypeAdapter(JsonValue).validate_python(row[0])

    def context(self,claim: Claim) -> JobContext:
        access=MeetingWorkerAccess(MINUTES_WRITER.name,Principal(kind='system'),claim)
        with meeting_call('meeting_job_context',claim.meeting_id,access) as facts, psycopg.connect(self.uri.get_secret_value()) as db:
            db.execute('set local role demobank_meetings')
            try:row=db.execute('select meetings.job_context(%s,%s)',(claim.event_id,claim.claim_token)).fetchone()
            except psycopg.ProgrammingError as error:
                if error.sqlstate in ('42501','P1001','P1002'):facts.update(allowed=False,reason='meeting_claim_or_consent_changed')
                raise
            if row is None:raise RuntimeError('Meeting job context returned no row')
            context=JobContext.model_validate(row[0])
            if context.meeting_id!=claim.meeting_id:
                facts.update(allowed=False,reason='meeting_scope_mismatch')
                raise PolicyDenied('Meeting job context does not match the claimed meeting')
            return context

    def renew(self,claim: Claim) -> None:
        with psycopg.connect(self.uri.get_secret_value()) as db:
            db.execute('set local role demobank_meetings');db.execute('select meetings.renew(%s,%s)',(claim.event_id,claim.claim_token))

    def chunk(self,claim: Claim,index: int,prompt: str,cue_ids: tuple[str,...]) -> ChunkSlot:
        with psycopg.connect(self.uri.get_secret_value()) as db:
            db.execute('set local role demobank_meetings')
            row=db.execute('select meetings.job_chunk(%s,%s,%s,%s,%s)',(claim.event_id,claim.claim_token,index,prompt,list(cue_ids))).fetchone()
            if row is None:raise RuntimeError('Chunk lookup returned no response')
            return ChunkSlot.model_validate(row[0])

    def save_chunk(self,claim: Claim,context: JobContext,index: int,prompt: str,chunk_hash: str,cue_ids: tuple[str,...],content: MinutesContent,
        input_tokens: int|None,output_tokens: int|None,charged_tokens: int) -> None:
        with psycopg.connect(self.uri.get_secret_value()) as db:
            db.execute('set local role demobank_meetings')
            db.execute('select meetings.save_job_chunk(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',(claim.event_id,claim.claim_token,context.consent_epoch,
                index,prompt,chunk_hash,list(cue_ids),Jsonb(content.model_dump(mode='json')),input_tokens,output_tokens,charged_tokens))

    def fetch_permit(self,claim: Claim) -> FetchPermit:
        with psycopg.connect(self.uri.get_secret_value()) as db:
            db.execute('set local role demobank_meetings')
            row=db.execute('select meetings.zoom_fetch_permit(%s,%s)',(claim.event_id,claim.claim_token)).fetchone()
            if row is None:raise RuntimeError('Zoom fetch permit returned no response')
            return FetchPermit.model_validate(row[0])

    def commit_transcript(self,claim: Claim,permit: FetchPermit,evidence: FetchedEvidence) -> int:
        with psycopg.connect(self.uri.get_secret_value()) as db:
            db.execute('set local role demobank_meetings')
            row=db.execute('select meetings.commit_zoom_transcript(%s,%s,%s,%s,%s,%s)',(claim.event_id,claim.claim_token,permit.consent_epoch,
                Jsonb([cue.model_dump(mode='json') for cue in evidence.cues]),Jsonb([segment.model_dump(mode='json') for segment in evidence.segments]),evidence.transcript_hash)).fetchone()
            if row is None:raise RuntimeError('Zoom evidence commit returned no transcript job')
            return int(row[0])

    def save(self,claim: Claim,context: JobContext,content: MinutesContent) -> JsonValue:
        access=MeetingWorkerAccess(MINUTES_WRITER.name,Principal(kind='system'),claim)
        with meeting_call('meeting_save_job_draft',context.meeting_id,access) as facts, psycopg.connect(self.uri.get_secret_value()) as db:
            db.execute('set local role demobank_meetings')
            try:
                row=db.execute('select meetings.save_job_draft(%s,%s,%s,%s,%s,%s)',(claim.event_id,claim.claim_token,context.source_hash,
                    context.revision,context.consent_epoch,Jsonb(content.model_dump(mode='json')))).fetchone()
            except psycopg.ProgrammingError as error:
                if error.sqlstate in ('42501','P1001','P1002'):facts.update(allowed=False,reason='meeting_claim_or_consent_changed')
                raise
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
        require_runtime_login(self.uri,'demobank_meetings')

    def apply_lifecycle(self,claim: Claim) -> None:
        with psycopg.connect(self.uri.get_secret_value()) as db:
            db.execute('set local role demobank_meetings')
            db.execute('select meetings.apply_lifecycle(%s,%s)',(claim.event_id,claim.claim_token))
