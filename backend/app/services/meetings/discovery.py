"""At the durable deadline, look only for this registered occurrence's note, then queue normal authorised ingestion."""
from pathlib import Path
import psycopg
from pydantic import ValidationError
from app.schemas.meeting_lifecycle import Claim
from app.services.meetings.database import WorkerDatabase
from app.services.meetings.my_notes import MyNotesReader,MyNotesError
from app.services.meetings.zoom import Zoom,ZoomFailure,refreshed_token


def discover(database: WorkerDatabase,claim: Claim,directory: Path) -> None:
    try:
        permit=database.discovery_permit(claim)
        if permit.source_exists:
            database.finish(claim)
            return
        token=refreshed_token(directory);provider=Zoom(token)
        try:
            owner=provider.owner()
            if owner.id!=permit.host_id or owner.account_id!=permit.account_id:
                raise PermissionError('The configured Zoom grant does not own this registered meeting; note discovery was refused')
        finally:provider.close()
        reader=MyNotesReader(token)
        try:notes=reader.notes(permit)
        finally:reader.close()
        if not notes.notes:database.report_missing_transcript(claim)
        elif len(notes.notes)==1:database.queue_discovered_note(claim,permit,notes.notes[0].note_id)
        else:raise ValueError('Zoom returned multiple notes for this occurrence; the RM must select or import the intended evidence before minutes are generated')
        database.finish(claim)
    except (MyNotesError,ZoomFailure):
        database.fail(claim,'zoom_api_failed')
        raise
    except PermissionError:
        database.report_missing_transcript(claim);database.reject(claim,'zoom_owner_or_note_mismatch')
        raise
    except (ValidationError,ValueError):
        database.report_missing_transcript(claim);database.reject(claim,'zoom_discovery_invalid')
        raise
    except psycopg.Error as error:
        if error.sqlstate in ('42501','P1001'):
            database.report_missing_transcript(claim);database.reject(claim,'zoom_permission_changed')
        raise
