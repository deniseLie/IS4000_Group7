"""Zoom connector limited to DemoBank-linked synthetic meetings. Credentials never enter prompts or logs."""
from datetime import datetime,timedelta,timezone
import json
import logging
from app.core.observability import log_event
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
import time
from urllib.parse import urlencode
from uuid import UUID

import httpx
from pydantic import AwareDatetime,BaseModel,ConfigDict,Field,SecretStr,StrictInt
from filelock import FileLock,Timeout
from app.schemas.zoom_scheduling import Cancellation

PREFIX='DemoBank meeting '

class Token(BaseModel):
    model_config=ConfigDict(extra='ignore',frozen=True,hide_input_in_errors=True)
    access_token: SecretStr
    refresh_token: SecretStr
    expires_in: StrictInt=Field(gt=0,le=86400)
    scope: str=Field(min_length=1,max_length=2000)

class PublicClient(BaseModel):
    model_config=ConfigDict(extra='ignore',frozen=True,hide_input_in_errors=True)
    client_id: str=Field(min_length=1,max_length=200)

class Meeting(BaseModel):
    model_config=ConfigDict(extra='ignore',frozen=True,hide_input_in_errors=True)
    id: StrictInt
    uuid: str
    topic: str
    join_url: str
    start_time: AwareDatetime
    duration: StrictInt
    status: str

class Owner(BaseModel):
    model_config=ConfigDict(extra='ignore',frozen=True,hide_input_in_errors=True)
    id: str=Field(min_length=1,max_length=128)
    account_id: str=Field(min_length=1,max_length=128)

class HostedMeeting(Meeting):
    host_id: str=Field(min_length=1,max_length=128)

class MeetingHeader(BaseModel):
    model_config=ConfigDict(extra='ignore',frozen=True,hide_input_in_errors=True)
    id: StrictInt
    topic: str

class MeetingIndex(BaseModel):
    model_config=ConfigDict(extra='ignore',frozen=True,hide_input_in_errors=True)
    meetings: tuple[MeetingHeader,...]=Field(max_length=300)
    next_page_token: str=Field(default='',max_length=2000)

class ZoomFailure(RuntimeError):pass

class CreateRefused(ZoomFailure):
    def __init__(self,status: int,body: str) -> None:
        self.status=status
        super().__init__('Zoom scheduling was refused: HTTP '+str(status)+'; response='+body)


def refreshed_token(directory: Path) -> SecretStr:
    from app.services.meetings.runtime import require_private_file
    path=directory/'zoom-tokens.json';require_private_file(path)
    lock_path=directory/'zoom-refresh.lock'
    descriptor=os.open(lock_path,os.O_RDWR|os.O_CREAT,0o600);os.close(descriptor);require_private_file(lock_path)
    try:
        with FileLock(lock_path,timeout=35,mode=0o600):return refresh_locked(directory)
    except Timeout as error:raise ZoomFailure('Zoom OAuth refresh remained busy for 35 seconds; retry after the API or worker finishes its current refresh') from error


def refresh_locked(directory: Path) -> SecretStr:
    """Serialize refresh-token rotation across the API and worker, and atomically replace the owner-only grant."""
    from app.services.meetings.runtime import require_private_file
    path=directory/'zoom-tokens.json';require_private_file(path);token=Token.model_validate_json(path.read_text())
    if time.time()-path.stat().st_mtime < token.expires_in-60:return token.access_token
    require_private_file(directory/'zoom-public-client.json')
    public=PublicClient.model_validate_json((directory/'zoom-public-client.json').read_text())
    with httpx.Client(timeout=25,follow_redirects=False) as client:
        response=client.post('https://zoom.us/oauth/token',data={'grant_type':'refresh_token','client_id':public.client_id,
            'refresh_token':token.refresh_token.get_secret_value()})
    if response.status_code!=200:raise ZoomFailure('Zoom OAuth refresh failed: HTTP '+str(response.status_code)+'; reauthorise the existing lab app. Provider response is not logged because it may contain credentials.')
    new=Token.model_validate(response.json())
    if set(new.scope.split())!=set(token.scope.split()):raise ZoomFailure('Refreshed OAuth scopes changed; review the grant before using it')
    with NamedTemporaryFile(mode='w',encoding='utf-8',dir=directory,prefix='zoom-refresh-',suffix='.tmp',delete=False) as out:
        json.dump(response.json(),out);out.flush();os.fsync(out.fileno());temporary=Path(out.name)
    temporary.replace(path)
    return new.access_token


class Zoom:
    def __init__(self,token: SecretStr) -> None:
        self.http=httpx.Client(base_url='https://api.zoom.us/v2',headers={'Authorization':'Bearer '+token.get_secret_value()},timeout=25,follow_redirects=False)

    def read(self,path: str) -> httpx.Response:
        for attempt in range(3):
            response=self.http.get(path)
            if response.status_code not in (429,500,502,503,504):return response
            log_event('zoom_read_retry',level=logging.WARNING,status=response.status_code,attempt=attempt+1,path=path)
            if attempt<2:time.sleep(attempt+1)
        raise ZoomFailure('Zoom read failed after 3 attempts: HTTP '+str(response.status_code)+'; path='+path+'; response='+response.text)

    def owner(self) -> Owner:
        response=self.read('/users/me')
        if response.status_code!=200:raise ZoomFailure('Zoom own-user identity failed: HTTP '+str(response.status_code)+'; authorise user:read:user for the automated meeting route. Response='+response.text)
        return Owner.model_validate(response.json())

    def create_bound(self,meeting_id: UUID,starts_at: datetime,owner: Owner) -> HostedMeeting:
        meeting=self.create(meeting_id,starts_at)
        response=self.read('/meetings/'+str(meeting.id))
        if response.status_code!=200:raise ZoomFailure('Zoom binding read failed: HTTP '+str(response.status_code)+'; reconcile the reserved creation before retrying')
        hosted=HostedMeeting.model_validate(response.json())
        if hosted.host_id!=owner.id or hosted.uuid!=meeting.uuid or hosted.topic!=meeting.topic:
            raise ZoomFailure('Zoom host or original occurrence changed during creation; no binding was saved')
        return hosted

    def hosted(self,provider_id: int,meeting_id: UUID,owner: Owner) -> HostedMeeting:
        self.inspect(provider_id,meeting_id)
        response=self.read('/meetings/'+str(provider_id))
        if response.status_code!=200:raise ZoomFailure('Registered Zoom meeting read failed: HTTP '+str(response.status_code))
        meeting=HostedMeeting.model_validate(response.json())
        if meeting.host_id!=owner.id:raise PermissionError('The configured own-user Zoom grant is not the registered meeting host')
        return meeting

    def create_schedule(self,meeting_id: UUID,starts_at: datetime,ends_at: datetime,agenda: str,owner: Owner) -> HostedMeeting:
        seconds=(ends_at-starts_at).total_seconds()
        if starts_at.utcoffset() is None or ends_at.utcoffset() is None or starts_at<=datetime.now(timezone.utc) or seconds%60 or not 60<=seconds<=86400:
            raise ValueError('The Zoom schedule needs a future aware start and a whole-minute duration from one minute to one day')
        if not agenda.strip() or len(agenda)>1000:raise ValueError('Zoom agenda must contain the agreed bounded meeting purpose')
        response=self.http.post('/users/me/meetings',json={'topic':PREFIX+str(meeting_id),'type':2,
            'start_time':starts_at.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),'duration':int(seconds//60),'timezone':'Asia/Singapore',
            'agenda':agenda,'settings':{'waiting_room':True,'join_before_host':False,'host_video':False,'participant_video':False,'use_pmi':False,'auto_recording':'none'}})
        if response.status_code!=201:
            if 400<=response.status_code<500:raise CreateRefused(response.status_code,response.text)
            raise ZoomFailure('Zoom scheduling failed: HTTP '+str(response.status_code)+'; response='+response.text)
        remote=Meeting.model_validate(response.json());hosted=self.hosted(remote.id,meeting_id,owner)
        if hosted.start_time!=starts_at.replace(microsecond=0) or hosted.duration!=int(seconds//60):raise ZoomFailure('Zoom persisted a different time or duration; reconcile the reservation before retrying')
        return hosted

    def reconcile(self,meeting_id: UUID,owner: Owner) -> HostedMeeting|None:
        """Resolve an interrupted creation by its exact registered topic; never create a second meeting on uncertainty."""
        token='';matches: list[int]=[]
        for _ in range(10):
            response=self.read('/users/me/meetings?'+urlencode({'type':'scheduled','page_size':300,**({'next_page_token':token} if token else {})}))
            if response.status_code!=200:raise ZoomFailure('Zoom creation reconciliation failed: HTTP '+str(response.status_code)+'; response='+response.text)
            page=MeetingIndex.model_validate(response.json());matches.extend(item.id for item in page.meetings if item.topic==PREFIX+str(meeting_id))
            token=page.next_page_token
            if not token:break
        if token:raise ZoomFailure('Zoom reconciliation exceeded ten pages; inspect the registered synthetic meeting in Zoom before retrying')
        if len(matches)>1:raise ZoomFailure('Multiple Zoom meetings have this registered topic; reconcile the duplicates explicitly before continuing')
        return None if not matches else self.hosted(matches[0],meeting_id,owner)

    def move_schedule(self,provider_id: int,meeting_id: UUID,starts_at: datetime,ends_at: datetime,agenda: str,owner: Owner) -> HostedMeeting:
        current=self.hosted(provider_id,meeting_id,owner);seconds=(ends_at-starts_at).total_seconds()
        if current.status!='waiting':raise ZoomFailure('This Zoom call has already started; do not change its scheduled occurrence')
        if seconds%60 or seconds<=0:raise ValueError('The rescheduled meeting requires a positive whole-minute duration')
        response=self.http.patch('/meetings/'+str(provider_id),json={'start_time':starts_at.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'duration':int(seconds//60),'agenda':agenda})
        if response.status_code!=204:raise ZoomFailure('Zoom reschedule failed: HTTP '+str(response.status_code)+'; response='+response.text)
        result=self.hosted(provider_id,meeting_id,owner)
        if result.start_time!=starts_at.replace(microsecond=0) or result.duration!=int(seconds//60):raise ZoomFailure('Zoom reschedule did not persist the agreed time and duration')
        return result

    def cancel_bound(self,provider_id: int,meeting_id: UUID,owner: Owner) -> Cancellation:
        response=self.read('/meetings/'+str(provider_id))
        if response.status_code==404:return 'already_absent'
        current=self.hosted(provider_id,meeting_id,owner)
        if current.status!='waiting':raise ZoomFailure('This Zoom call has started; end the call rather than deleting its scheduled occurrence')
        response=self.http.delete('/meetings/'+str(provider_id))
        if response.status_code==404:return 'already_absent'
        if response.status_code!=204:raise ZoomFailure('Zoom cancellation failed: HTTP '+str(response.status_code)+'; response='+response.text)
        return 'deleted'

    def create(self,meeting_id: UUID,starts_at: datetime) -> Meeting:
        if starts_at.utcoffset() is None or starts_at<=datetime.now(timezone.utc):raise ValueError('Zoom meeting requires a future timezone-aware start')
        topic=PREFIX+str(meeting_id)
        response=self.http.post('/users/me/meetings',json={'topic':topic,'type':2,'start_time':starts_at.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
          'duration':30,'timezone':'Asia/Singapore','agenda':'DemoBank synthetic meeting: discuss the agreed agenda.',
          'settings':{'waiting_room':True,'join_before_host':False,'host_video':False,'participant_video':False,'use_pmi':False,'auto_recording':'none'}})
        if response.status_code!=201:raise ZoomFailure('Zoom create failed: HTTP '+str(response.status_code)+'; response='+response.text)
        meeting=Meeting.model_validate(response.json())
        if meeting.topic!=topic or meeting.start_time!=starts_at.replace(microsecond=0):raise ZoomFailure('Zoom returned an unexpected topic or start time; inspect the lab meeting before retrying')
        return meeting

    def inspect(self,provider_id: int,meeting_id: UUID) -> Meeting:
        response=self.read('/meetings/'+str(provider_id))
        if response.status_code!=200:raise ZoomFailure('Zoom lab read failed: HTTP '+str(response.status_code)+'; response='+response.text)
        meeting=Meeting.model_validate(response.json())
        if meeting.topic!=PREFIX+str(meeting_id):raise PermissionError('This Zoom meeting is outside the isolated prototype')
        return meeting

    def reschedule(self,provider_id: int,meeting_id: UUID,starts_at: datetime) -> Meeting:
        self.inspect(provider_id,meeting_id)
        if starts_at.utcoffset() is None:raise ValueError('Reschedule needs a timezone-aware time')
        response=self.http.patch('/meetings/'+str(provider_id),json={'start_time':starts_at.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')})
        if response.status_code!=204:raise ZoomFailure('Zoom reschedule failed: HTTP '+str(response.status_code)+'; response='+response.text)
        result=self.inspect(provider_id,meeting_id)
        if result.start_time!=starts_at.replace(microsecond=0):raise ZoomFailure('Zoom did not persist the requested reschedule')
        return result

    def cancel(self,provider_id: int,meeting_id: UUID) -> None:
        self.inspect(provider_id,meeting_id)
        response=self.http.delete('/meetings/'+str(provider_id))
        if response.status_code!=204:raise ZoomFailure('Zoom cancellation failed: HTTP '+str(response.status_code)+'; response='+response.text)

    def close(self) -> None:self.http.close()
