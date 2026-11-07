"""Dedicated Zoom ingress: original-byte HMAC, closed IDs-only writes, no customer API exposure.

Run the listener separately from the customer API. Only this app is exposed to
Zoom; it serves no docs, static files, banking endpoints or credentials.
"""
from datetime import datetime
from hashlib import sha256
import hmac,re,time,logging
from typing import Annotated,Literal,TypeAlias
from uuid import UUID
import psycopg
from fastapi import FastAPI,HTTPException,Request
from pydantic import BaseModel,ConfigDict,Field,SecretStr,StrictInt,TypeAdapter,AwareDatetime
from starlette.concurrency import run_in_threadpool
from app.core.observability import log_event
from app.services.meetings.database import require_runtime_login

class EventInputError(ValueError):pass

class External(BaseModel):
    model_config=ConfigDict(extra='ignore',frozen=True,hide_input_in_errors=True)

class NoteObject(External):
    note_id: str=Field(min_length=1,max_length=200)
    meeting_id: str|None=Field(default=None,max_length=200)

class NotePayload(External):
    account_id: str=Field(min_length=1,max_length=128)
    operator_id: str=Field(min_length=1,max_length=128)
    object: NoteObject

class NoteEvent(External):
    event: Literal['my_notes.transcript_generated','my_notes.note_completed']
    event_ts: StrictInt=Field(ge=0)
    payload: NotePayload

class CallObject(External):
    id: StrictInt|str
    uuid: str=Field(min_length=1,max_length=200)
    host_id: str=Field(min_length=1,max_length=128)
    start_time: AwareDatetime
    end_time: AwareDatetime|None=None

class CallPayload(External):
    account_id: str=Field(min_length=1,max_length=128)
    object: CallObject

class CallEvent(External):
    event: Literal['meeting.started','meeting.ended']
    event_ts: StrictInt=Field(ge=0)
    payload: CallPayload

class ChallengePayload(External):
    plainToken: str=Field(min_length=1,max_length=500)

class Challenge(External):
    event: Literal['endpoint.url_validation']
    event_ts: StrictInt=Field(ge=0)
    payload: ChallengePayload

Webhook: TypeAlias=Annotated[NoteEvent|CallEvent|Challenge,Field(discriminator='event')]


def verified(raw: bytes,timestamp: str,signature: str,secret: SecretStr,now: int) -> None:
    if not re.fullmatch(r'[0-9]{1,12}',timestamp) or not re.fullmatch(r'v0=[a-f0-9]{64}',signature):raise EventInputError('Zoom signature headers are malformed')
    if abs(now-int(timestamp))>300:raise EventInputError('Zoom signature timestamp is outside the five-minute window')
    expected='v0='+hmac.new(secret.get_secret_value().encode(),b'v0:'+timestamp.encode()+b':'+raw,'sha256').hexdigest()
    if not hmac.compare_digest(expected,signature):raise EventInputError('Zoom signature does not match the original request bytes')


class IngressDatabase:
    def __init__(self,uri: SecretStr) -> None:self.uri=uri

    def execute(self,query: str,params: tuple[str|int|datetime|None,...]) -> str:
        for attempt in range(1,4):
            try:
                with psycopg.connect(self.uri.get_secret_value(),connect_timeout=5) as db:
                    db.execute('set local role demobank_meeting_ingress')
                    row=db.execute(query,params).fetchone()
                    if row is None:raise RuntimeError('Zoom ingress SQL returned no durable receipt')
                    return str(row[0])
            except (psycopg.OperationalError,psycopg.errors.SerializationFailure,psycopg.errors.DeadlockDetected):
                if attempt==3:raise
                log_event('zoom_ingress_database_retry',level=logging.WARNING,attempt=attempt)
                time.sleep(attempt)
        raise RuntimeError('Zoom ingress retries ended without a receipt')

    def accept_note(self,event: NoteEvent) -> str:
        payload=event.payload
        return self.execute('select meetings.accept_zoom_note(%s,%s,%s,%s,%s)',
            (payload.account_id,payload.operator_id,payload.object.meeting_id,payload.object.note_id,event.event))

    def accept_call(self,event: CallEvent) -> str:
        obj=event.payload.object
        if isinstance(obj.id,str) and not re.fullmatch(r'[0-9]{1,16}',obj.id):raise EventInputError('Zoom meeting number must contain only decimal digits')
        return self.execute('select meetings.accept_zoom_call(%s,%s,%s,%s,%s,%s,%s)',
            (event.payload.account_id,obj.host_id,int(obj.id),obj.uuid,event.event,obj.start_time,obj.end_time))

    def verify_identity(self) -> None:
        require_runtime_login(self.uri,'demobank_meeting_ingress')


def application(database: IngressDatabase,secret: SecretStr) -> FastAPI:
    if len(secret.get_secret_value())<16:raise ValueError('Configure the complete Zoom-issued webhook secret; values shorter than 16 characters are refused')
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    @app.post('/zoom/events')
    async def receive(request: Request) -> dict[str,str]:
        raw=bytearray()
        async for part in request.stream():
            if len(raw)+len(part)>65536:raise HTTPException(413,'Zoom event exceeds the 64 KiB limit')
            raw.extend(part)
        try:verified(bytes(raw),request.headers.get('x-zm-request-timestamp',''),request.headers.get('x-zm-signature',''),secret,int(time.time()))
        except EventInputError as error:raise HTTPException(401,str(error)) from error
        try:
            event=TypeAdapter(Webhook).validate_json(bytes(raw))
            if isinstance(event,Challenge):
                token=event.payload.plainToken
                return {'plainToken':token,'encryptedToken':hmac.new(secret.get_secret_value().encode(),token.encode(),'sha256').hexdigest()}
            receipt=await run_in_threadpool(database.accept_note if isinstance(event,NoteEvent) else database.accept_call,event)
        except ValueError as error:raise HTTPException(422,'Invalid supported Zoom event; a note must be linked to a registered meeting') from error
        except psycopg.Error as error:
            status={'42501':403,'P1001':409,'23505':409,'22023':422}.get(error.sqlstate)
            if status is None:raise
            raise HTTPException(status,'Zoom event refused: '+str(error).partition('\n')[0]) from error
        return {'status':'durably_received','receipt_id':receipt}
    return app


def create_app() -> FastAPI:
    """Uvicorn factory for the dedicated listener; never expose the customer API through its tunnel."""
    from app.core.config import settings
    if not settings.meetings_enabled or not settings.zoom_capture_enabled:raise RuntimeError('Enable MEETINGS_ENABLED and ZOOM_CAPTURE_ENABLED for this dedicated listener')
    if settings.zoom_ingress_database_url is None or settings.zoom_webhook_secret is None:
        raise RuntimeError('Configure ZOOM_INGRESS_DATABASE_URL and ZOOM_WEBHOOK_SECRET privately before starting the listener')
    database=IngressDatabase(settings.zoom_ingress_database_url)
    database.verify_identity()
    return application(database,settings.zoom_webhook_secret)
