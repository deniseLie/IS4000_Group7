"""The Briefing screen and automatic RM preparation use the lifecycle's one reference-only saved brief.

The 0118 RPCs still identify owned appointments and retain historical copilot versions. Their stored content is not
copied into a current brief, sent to a model, or treated as an approval of the current lifecycle version.
"""
import asyncio
from datetime import datetime
from typing import Literal,NamedTuple,NoReturn
from uuid import UUID,uuid4,uuid5
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime,BaseModel,ConfigDict,Field,JsonValue,TypeAdapter
from app.core.errors import ApiError
from app.schemas.contracts import RequestContext
from app.schemas.meeting_preparation import PreparationInputs,StoredBrief
from app.services.meetings import assistant,preparation
from app.services.meetings.database import ApplicationDatabase,MeetingStoreError
from app.services.rm import records


class WireModel(BaseModel):
    model_config=ConfigDict(extra='ignore',frozen=True,hide_input_in_errors=True)


class Meeting(WireModel):
    meeting_id: UUID
    customer_id: int=Field(strict=True,gt=0)
    when: str
    starts_at: AwareDatetime
    format: Literal['video','phone','in_person']
    purpose: str|None
    topics: tuple[str,...]


class HistoricalBrief(WireModel):
    brief_id: UUID
    version: int=Field(strict=True,gt=0)


class OwnedMeeting(WireModel):
    meeting: Meeting
    brief: HistoricalBrief|None


class Notice(WireModel):
    notice_id: UUID


class SessionNotice(WireModel):
    notice: Notice


class Snapshot(NamedTuple):
    owned: OwnedMeeting
    frame: PreparationInputs|None
    stored: StoredBrief|None


def database_error(error: MeetingStoreError) -> ApiError:
    status={'42501':403,'P0002':404,'P1001':409,'23505':409}.get(error.code)
    if status is None and error.code.startswith('22'):status=422
    return ApiError(status or 503,'meeting_brief_failed',str(error))


async def snapshot(meeting: UUID,ctx: RequestContext) -> Snapshot:
    value=await records.rpc('rm_meeting_brief',{'p_meeting_id':str(meeting)},ctx,'rm.meeting_brief')
    owned=OwnedMeeting.model_validate(value['data'])
    database=ApplicationDatabase(ctx);claims=preparation.caller(ctx)
    try:frame=await preparation.inputs(meeting,database,claims)
    except MeetingStoreError as error:
        if error.code=='P0002':return Snapshot(owned,None,None)
        raise database_error(error) from error
    try:stored=await preparation.current(meeting,database,claims)
    except MeetingStoreError as error:raise database_error(error) from error
    return Snapshot(owned,frame,stored)


def created_text(value: datetime) -> str:
    return value.astimezone(ZoneInfo('Asia/Singapore')).strftime('%a %d %b %Y, %I:%M %p')+' (Singapore time)'


def render(saved: Snapshot) -> dict[str,JsonValue]:
    result: dict[str,JsonValue]={'meeting':saved.owned.meeting.model_dump(mode='json'),
        'brief':None,'legacy_brief_available':saved.owned.brief is not None}
    if saved.stored is None:return result
    if saved.frame is None:raise RuntimeError('A saved lifecycle brief requires its current scoped source frame')
    prepared=preparation.render(saved.frame,saved.stored)
    brief=assistant.reply(prepared,{'client_id':saved.owned.meeting.customer_id,'appointment_id':str(saved.owned.meeting.meeting_id)})
    manifest={item.source_id:item for item in saved.stored.source_manifest}
    withdrawn: list[JsonValue]=[];removed: list[JsonValue]=[];seen: set[str]=set()
    for source in saved.frame.removed_sources:
        reference=manifest[source.source_id]
        on=source.removed_on.isoformat() if source.removed_on else 'an unrecorded date'
        if reference.point_id is not None and source.reason=='removed':
            removed.append({'point_id':str(reference.point_id),'on':on})
        elif reference.category is not None and reference.category not in seen:
            seen.add(reference.category);withdrawn.append({'category':reference.category,'on':on})
    brief.update(brief_id=str(prepared.brief_id),version=prepared.card.version,brief_status=prepared.card.status,
        created=created_text(saved.stored.created_at),stale=prepared.sources_changed,withdrawn=withdrawn,removed_points=removed,
        withheld=prepared.card.withheld,consent=prepared.card.consent)
    result['brief']=TypeAdapter(dict[str,JsonValue]).validate_python(brief)
    return result


async def read(meeting: UUID,ctx: RequestContext) -> dict[str,JsonValue]:
    return render(await snapshot(meeting,ctx))


async def open_workflow(meeting: UUID,ctx: RequestContext) -> None:
    saved=await snapshot(meeting,ctx)
    if saved.frame is not None:return
    purpose=saved.owned.meeting.purpose
    if purpose is None or not purpose.strip():
        raise ApiError(409,'meeting_purpose_required','Agree a meeting purpose before preparing the briefing.')
    database=ApplicationDatabase(ctx);claims=preparation.caller(ctx)
    try:
        notice=SessionNotice.model_validate(await asyncio.to_thread(database.call,'meeting_identity',{},claims))
        await asyncio.to_thread(database.call,'meeting_start_workflow',{'p_meeting_id':meeting,'p_notice_id':notice.notice.notice_id,
            'p_purpose':purpose,'p_outcome':purpose,'p_command_id':uuid5(meeting,'brief-workflow:'+str(notice.notice.notice_id)+':'+purpose)},claims)
    except MeetingStoreError as error:
        if error.code=='23505' and (await snapshot(meeting,ctx)).frame is not None:return
        raise database_error(error) from error


async def prepare(meeting: UUID,ctx: RequestContext) -> dict[str,JsonValue]:
    await open_workflow(meeting,ctx)
    saved=await snapshot(meeting,ctx)
    try:
        if saved.frame is not None and saved.stored is not None and preparation.render(saved.frame,saved.stored).sources_changed:
            await preparation.refresh(meeting,ctx,uuid5(meeting,'prepare-current:'+saved.frame.source_hash+':'+str(saved.stored.version)))
        else:await preparation.prepare(meeting,ctx)
    except MeetingStoreError as error:raise database_error(error) from error
    return await read(meeting,ctx)


async def refresh(meeting: UUID,ctx: RequestContext) -> dict[str,JsonValue]:
    await open_workflow(meeting,ctx)
    try:await preparation.refresh(meeting,ctx,uuid4())
    except MeetingStoreError as error:raise database_error(error) from error
    return await read(meeting,ctx)


async def refuse_approval(ctx: RequestContext,code: str,message: str) -> NoReturn:
    database=ApplicationDatabase(ctx)
    await asyncio.to_thread(database.record_refusal,'meeting_brief_approval','P1001',preparation.caller(ctx))
    raise ApiError(409,code,message)


async def approve(meeting: UUID,brief: UUID,version: int,command: UUID,ctx: RequestContext) -> dict[str,JsonValue]:
    saved=await snapshot(meeting,ctx)
    if saved.stored is None or saved.frame is None:raise ApiError(404,'meeting_brief_missing','Prepare the current briefing before approving it.')
    if saved.stored.brief_id!=brief or saved.stored.version!=version:
        await refuse_approval(ctx,'meeting_brief_replaced','This approval references a replaced briefing. Open the current version first.')
    prepared=preparation.render(saved.frame,saved.stored)
    if prepared.sources_changed:await refuse_approval(ctx,'meeting_brief_changed','Source records or sharing choices changed. Prepare a new version before approval.')
    database=ApplicationDatabase(ctx);claims=preparation.caller(ctx)
    try:
        result=await asyncio.to_thread(database.call,'meeting_decide_brief',{'p_meeting_id':meeting,'p_version':version,
            'p_content_hash':saved.stored.content_hash,'p_revision':saved.stored.revision,'p_outcome':'approve','p_note':None,'p_command_id':command},claims)
    except MeetingStoreError as error:raise database_error(error) from error
    approved=StoredBrief.model_validate(result)
    return {'brief_id':str(approved.brief_id),'version':approved.version,'status':approved.status}


async def upcoming(ctx: RequestContext) -> list[dict[str,JsonValue]]:
    response=await records.rpc('rm_meeting_briefs',{},ctx,'rm.meeting_briefs')
    rows=TypeAdapter(list[dict[str,JsonValue]]).validate_python(response['data'])
    result: list[dict[str,JsonValue]]=[]
    for row in rows:
        meeting=UUID(str(row['meeting_id']));saved=await snapshot(meeting,ctx)
        prepared=preparation.render(saved.frame,saved.stored) if saved.frame is not None and saved.stored is not None else None
        result.append({**row,'brief_version':prepared.card.version if prepared else None,
            'brief_status':prepared.card.status if prepared else None,'brief_created':created_text(saved.stored.created_at) if saved.stored else None,
            'stale':prepared.sources_changed if prepared else None})
    return result
