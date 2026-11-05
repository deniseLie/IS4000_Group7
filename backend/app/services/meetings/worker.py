"""Restricted meeting queue/retention runner. Start with python -m app.services.meetings.worker.

MEETINGS_DATABASE_URL must name a non-owner LOGIN in demobank_meetings. No service key or RM JWT is accepted.
Transcript jobs use the separate Minutes Writer graph. Unsupported pre-meeting events fail explicitly.
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
from app.services.meetings.graph import process_transcript


async def dispatch(database: WorkerDatabase,claim: Claim) -> None:
    if claim.event_type=='transcript.ready':
        await process_transcript(database,claim)
        return
    if claim.event_type=='brief.requested':
        await asyncio.to_thread(database.reject,claim,'brief_not_available')
        return
    await asyncio.to_thread(database.apply_lifecycle,claim)
    await asyncio.to_thread(database.finish,claim)


async def run(database: WorkerDatabase) -> None:
    await asyncio.to_thread(database.verify_identity)
    while True:
        await asyncio.to_thread(database.purge_transcripts)
        claim=await asyncio.to_thread(database.claim)
        if claim is None:
            await asyncio.sleep(settings.worker_poll_seconds)
            continue
        try:await dispatch(database,claim)
        except (LLMError,ValidationError,psycopg.ProgrammingError) as error:
            log_event('meeting_job_failed',level=logging.WARNING,event_id=claim.event_id,error_type=type(error).__name__,sqlstate=getattr(error,'sqlstate',None))


async def main() -> None:
    if not settings.meetings_enabled:raise RuntimeError('Enable MEETINGS_ENABLED before starting the meeting worker')
    if settings.meetings_database_url is None:raise RuntimeError('Configure MEETINGS_DATABASE_URL with the restricted meeting LOGIN')
    await run(WorkerDatabase(settings.meetings_database_url))

if __name__=='__main__':asyncio.run(main())
