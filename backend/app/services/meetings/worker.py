"""Restricted meeting queue/retention runner. Start with python -m app.services.meetings.worker.

MEETINGS_DATABASE_URL must name a non-owner LOGIN in demobank_meetings. No service key or RM JWT is accepted.
The parent lifecycle graph routes events. Preparation requests wait for the assigned RM; the runner has no banking readers.
"""
import asyncio
import logging
from app.core.observability import log_event
import psycopg
from pydantic import ValidationError
from app.core.config import settings
from app.schemas.meeting_lifecycle import Claim
from app.services.llm_gateway import LLMError
from app.services.meetings.database import WorkerDatabase
from app.services.meetings.lifecycle import dispatch
from app.services.meetings.my_notes import MyNotesError
from app.services.meetings.zoom import ZoomFailure


async def run(database: WorkerDatabase) -> None:
    if settings.meetings_checkpoint_signing_key is None:raise RuntimeError('Configure the stable private MEETINGS_CHECKPOINT_SIGNING_KEY before starting the meeting worker')
    await asyncio.to_thread(database.verify_identity)
    while True:
        await asyncio.to_thread(database.purge_transcripts)
        await asyncio.to_thread(database.schedule_due)
        await asyncio.to_thread(database.schedule_graph_updates)
        claim=await asyncio.to_thread(database.claim)
        if claim is None:
            await asyncio.sleep(settings.worker_poll_seconds)
            continue
        try:await dispatch(database,claim)
        except (LLMError,ValidationError,psycopg.ProgrammingError,psycopg.DataError,psycopg.IntegrityError,MyNotesError,ZoomFailure,PermissionError,ValueError) as error:
            log_event('meeting_job_failed',level=logging.WARNING,event_id=claim.event_id,error_type=type(error).__name__,sqlstate=getattr(error,'sqlstate',None))


async def main() -> None:
    if not settings.meetings_enabled:raise RuntimeError('Enable MEETINGS_ENABLED before starting the meeting worker')
    if settings.meetings_database_url is None:raise RuntimeError('Configure MEETINGS_DATABASE_URL with the restricted meeting LOGIN')
    await run(WorkerDatabase(settings.meetings_database_url))

if __name__=='__main__':asyncio.run(main())
