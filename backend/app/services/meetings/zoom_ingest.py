"""Permission-fenced provider fetch. Only selected redacted cues reach the Minutes Writer.

No banking readers, RM JWT, arbitrary URL downloads or model tools are available here.
Provider note IDs stay in private SQL; the event queue contains only local UUIDs.
"""
from hashlib import sha256
import json
from pathlib import Path
import psycopg
from pydantic import ValidationError
from app.schemas.meeting_lifecycle import Claim,Cue
from app.schemas.zoom_capture import FetchedEvidence,SegmentPosition
from app.services.privacy import redact_details
from app.services.meetings.database import WorkerDatabase
from app.services.meetings.my_notes import MyNotesReader,MyNotesError,TranscriptNotReady,Transcript,milliseconds
from app.services.meetings.zoom import Zoom,refreshed_token,ZoomFailure


def transcript_evidence(transcript: Transcript) -> FetchedEvidence:
    aliases={speaker.speaker_id:'speaker'+str(index) for index,speaker in enumerate(transcript.speakers,1)}
    cues=[];segments=[]
    for index,item in enumerate(transcript.items,1):
        cue_id='cue'+str(index)
        safe,_=redact_details(item.text)
        cues.append(Cue(cue_id=cue_id,speaker='unmapped',start_seconds=milliseconds(item.start_time)//1000,text=safe))
        segments.append(SegmentPosition(cue_id=cue_id,speaker_key=aliases[item.speaker_id],start_ms=milliseconds(item.start_time),end_ms=milliseconds(item.end_time)))
    evidence=FetchedEvidence(cues=tuple(cues),segments=tuple(segments),transcript_hash=sha256(transcript.model_dump_json().encode()).hexdigest())
    if len(json.dumps([cue.model_dump(mode='json') for cue in evidence.cues],ensure_ascii=False).encode())>128000:
        raise ValueError('Zoom evidence exceeds the 128000-byte meeting intake limit; no partial transcript was saved')
    return evidence


def fetch_transcript(database: WorkerDatabase,claim: Claim,directory: Path) -> None:
    try:
        permit=database.fetch_permit(claim)
        if permit.source_exists:
            database.finish(claim)
            return
        token=refreshed_token(directory)
        provider=Zoom(token)
        try:
            owner=provider.owner()
            if owner.id!=permit.host_id or owner.account_id!=permit.account_id:
                raise PermissionError('Configured Zoom grant does not own the queued account and host; no notes were requested')
        finally:provider.close()
        reader=MyNotesReader(token)
        try:evidence=transcript_evidence(reader.transcript(permit,permit.note_id))
        finally:reader.close()
        database.commit_transcript(claim,permit,evidence)
        database.finish(claim)
    except TranscriptNotReady:
        database.fail(claim,'zoom_transcript_not_ready')
        raise
    except (MyNotesError,ZoomFailure):
        database.fail(claim,'zoom_api_failed')
        raise
    except (ValidationError,ValueError):
        database.reject(claim,'zoom_evidence_invalid')
        raise
    except PermissionError:
        database.reject(claim,'zoom_owner_or_note_mismatch')
        raise
    except psycopg.Error as error:
        if error.sqlstate=='P1003':database.fail(claim,'zoom_call_not_ended')
        elif error.sqlstate in ('42501','P1001'):database.reject(claim,'zoom_permission_changed')
        elif error.sqlstate in ('22023','23505'):database.reject(claim,'zoom_evidence_conflict')
        raise
