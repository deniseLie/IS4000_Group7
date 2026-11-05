"""Zoom connector limited to DemoBank-linked synthetic meetings. Credentials never enter prompts or logs."""
from datetime import datetime,timedelta,timezone
import json
import logging
from app.core.observability import log_event
import os
from pathlib import Path
import time
from uuid import UUID

import httpx
from pydantic import AwareDatetime,BaseModel,ConfigDict,SecretStr,StrictInt

PREFIX='DemoBank meeting '

class Token(BaseModel):
    model_config=ConfigDict(extra='ignore',frozen=True)
    access_token: SecretStr
    refresh_token: SecretStr
    expires_in: StrictInt
    scope: str

class Meeting(BaseModel):
    model_config=ConfigDict(extra='ignore',frozen=True)
    id: StrictInt
    uuid: str
    topic: str
    join_url: str
    start_time: AwareDatetime
    duration: StrictInt
    status: str

class ZoomFailure(RuntimeError):pass


def refreshed_token(directory: Path) -> SecretStr:
    path=directory/'zoom-tokens.json';token=Token.model_validate_json(path.read_text())
    if path.stat().st_mode & 0o077:raise PermissionError('Zoom credential file must be owner-only')
    if time.time()-path.stat().st_mtime < token.expires_in-60:return token.access_token
    public=json.loads((directory/'zoom-public-client.json').read_text())
    with httpx.Client(timeout=25,follow_redirects=False) as client:
        response=client.post('https://zoom.us/oauth/token',data={'grant_type':'refresh_token','client_id':public['client_id'],
            'refresh_token':token.refresh_token.get_secret_value()})
    if response.status_code!=200:raise ZoomFailure('Zoom OAuth refresh failed: HTTP '+str(response.status_code)+'; reauthorise the existing lab app. Provider response is not logged because it may contain credentials.')
    new=Token.model_validate(response.json())
    if set(new.scope.split())!=set(token.scope.split()):raise ZoomFailure('Refreshed OAuth scopes changed; review the grant before using it')
    temporary=directory/'refresh-hybrid.tmp'
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as out:json.dump(response.json(),out)
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
