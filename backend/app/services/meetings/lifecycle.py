"""Durable per-meeting LangGraph with signed ID-only checkpoints and persistent approval/provider interrupts.

SQL owns permission and exact-version effects. Queue acknowledgement waits until the graph checkpoint is durable;
model chunks and committed drafts survive a retry. Credentials, banking records and transcript cues stay outside state.
"""
import asyncio
from uuid import UUID,uuid4
from langgraph.graph import START,END,StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command,interrupt
from pydantic import SecretStr
from app.core.config import settings
from app.schemas.meeting_lifecycle import Claim
from app.schemas.meeting_reviews import LifecycleState
from app.schemas.meetings import MeetingStatus
from app.services.meetings.database import WorkerDatabase
from app.services.meetings.graph import process_transcript
from app.services.meetings.zoom_ingest import fetch_transcript
from app.services.meetings.discovery import discover
from app.services.meetings.checkpoints import MeetingCheckpointSaver
from app.services.checkpoint_codec import CheckpointIntegrityError
from app.services.meetings import scheduling


def build(database: WorkerDatabase,claim: Claim,run_id: UUID,key: SecretStr) -> CompiledStateGraph:
    async def resume(state: LifecycleState) -> dict[str,MeetingStatus|int]:
        result=await asyncio.to_thread(database.checkpoint,claim)
        status=MeetingStatus.model_validate(result)
        if status.meeting_id!=claim.meeting_id:raise PermissionError('Claimed meeting ID does not match the persisted queue event')
        if state.meeting_id!=claim.meeting_id:raise PermissionError('The saved lifecycle state belongs to another meeting')
        return {'status':status,'event_id':claim.event_id}
    async def fetch(state: LifecycleState) -> dict[str,object]:
        if settings.zoom_oauth_directory is None:
            await asyncio.to_thread(database.reject,claim,'zoom_not_configured')
            raise ValueError('Queued Zoom transcript requires the configured own-user OAuth directory')
        await asyncio.to_thread(fetch_transcript,database,claim,settings.zoom_oauth_directory)
        return {}
    async def minutes(state: LifecycleState) -> dict[str,object]:
        await process_transcript(database,claim)
        return {}
    async def find_note(state: LifecycleState) -> dict[str,object]:
        if settings.zoom_oauth_directory is None:
            await asyncio.to_thread(database.report_missing_transcript,claim)
            await asyncio.to_thread(database.reject,claim,'zoom_not_configured')
            raise ValueError('The transcript deadline needs a configured own-user Zoom OAuth directory')
        await asyncio.to_thread(discover,database,claim,settings.zoom_oauth_directory)
        return {}
    async def project(state: LifecycleState) -> dict[str,object]:
        await asyncio.to_thread(database.apply_lifecycle,claim)
        await asyncio.to_thread(database.finish,claim)
        return {}
    async def provision(state: LifecycleState) -> dict[str,object]:
        if settings.zoom_oauth_directory is None:
            await asyncio.to_thread(database.reject,claim,'zoom_not_configured')
            raise ValueError('Automatic scheduling requires the configured private own-user Zoom OAuth directory')
        await asyncio.to_thread(scheduling.schedule,database,claim,settings.zoom_oauth_directory)
        return {}
    async def cancel_zoom(state: LifecycleState) -> dict[str,object]:
        if settings.zoom_oauth_directory is None:
            await asyncio.to_thread(database.reject,claim,'zoom_not_configured')
            raise ValueError('Automatic cancellation requires the configured private own-user Zoom OAuth directory')
        await asyncio.to_thread(scheduling.cancel,database,claim,settings.zoom_oauth_directory)
        return {}
    def route(state: LifecycleState) -> str:
        if settings.zoom_capture_enabled and claim.event_type in ('meeting.scheduled','meeting.rescheduled'):return 'provision_registered_zoom'
        if settings.zoom_capture_enabled and claim.event_type=='meeting.cancelled':return 'cancel_registered_zoom'
        return {'transcript.fetch':'fetch_authorized_transcript','transcript.discover':'discover_captured_note','transcript.ready':'write_grounded_minutes'}.get(claim.event_type,'project_lifecycle_and_wait')
    async def project_phase(state: LifecycleState) -> dict[str,MeetingStatus]:
        result=await asyncio.to_thread(database.graph_status,claim.meeting_id,run_id)
        return {'status':MeetingStatus.model_validate(result)}
    def phase_route(state: LifecycleState) -> str:
        if state.status is None:raise RuntimeError('The meeting graph has no persisted lifecycle phase')
        return END if state.status.stage in ('done','cancelled') else 'wait_for_lifecycle_event'
    async def wait_for_event(state: LifecycleState) -> dict[str,object]:
        if state.status is None:raise RuntimeError('A lifecycle wait requires the current persisted phase')
        resumed=interrupt(state.status.model_dump(mode='json'))
        if type(resumed) is not int or resumed!=claim.event_id:raise PermissionError('Only the current fenced meeting event can resume this lifecycle wait')
        return {}
    graph=StateGraph(LifecycleState);graph.add_node('resume_fenced_claim',resume)
    graph.add_node('fetch_authorized_transcript',fetch);graph.add_node('write_grounded_minutes',minutes);graph.add_node('project_lifecycle_and_wait',project)
    graph.add_node('discover_captured_note',find_note)
    graph.add_node('provision_registered_zoom',provision);graph.add_node('cancel_registered_zoom',cancel_zoom)
    graph.add_edge(START,'resume_fenced_claim');graph.add_conditional_edges('resume_fenced_claim',route)
    graph.add_node('project_durable_phase',project_phase);graph.add_node('wait_for_lifecycle_event',wait_for_event)
    for node in ('fetch_authorized_transcript','discover_captured_note','write_grounded_minutes','project_lifecycle_and_wait','provision_registered_zoom','cancel_registered_zoom'):graph.add_edge(node,'project_durable_phase')
    graph.add_conditional_edges('project_durable_phase',phase_route);graph.add_edge('wait_for_lifecycle_event','resume_fenced_claim')
    return graph.compile(checkpointer=MeetingCheckpointSaver(database,claim.meeting_id,run_id,key))


async def dispatch(database: WorkerDatabase,claim: Claim) -> None:
    key=settings.meetings_checkpoint_signing_key
    if key is None:raise RuntimeError('Configure the stable private MEETINGS_CHECKPOINT_SIGNING_KEY before processing meeting events')
    run_id=uuid4();await asyncio.to_thread(database.graph_open,claim,run_id)
    graph=build(database,claim,run_id,key)
    config={'configurable':{'thread_id':str(claim.meeting_id),'checkpoint_ns':''}}
    try:saved=await graph.aget_state(config)
    except CheckpointIntegrityError:
        await asyncio.to_thread(database.reject,claim,'meeting_checkpoint_invalid')
        raise
    if saved.values and saved.values.get('event_id')==claim.event_id and (saved.interrupts or not saved.next):
        # The checkpoint survived but the process stopped before queue acknowledgement. Do not repeat the event.
        await asyncio.to_thread(database.finish,claim)
    elif saved.interrupts:await graph.ainvoke(Command(resume=claim.event_id),config,durability='sync')
    elif saved.next:await graph.ainvoke(None,config,durability='sync')
    else:await graph.ainvoke(LifecycleState(meeting_id=claim.meeting_id,event_id=claim.event_id,status=None),config,durability='sync')
    await asyncio.to_thread(database.graph_close,claim.meeting_id,run_id)
