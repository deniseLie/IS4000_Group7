"""My Notes API reader restricted by a registered synthetic meeting and its returned note IDs.

Only transcript segments are returned to the evidence layer. Manual and generated
note content, sharing URLs and display names never become banking or model evidence.
No successful transcript is fabricated when capture has not produced one.
"""
import logging,time
from urllib.parse import quote,urlencode,urlsplit
import httpx
from pydantic import BaseModel,ConfigDict,Field,SecretStr,model_validator
from app.core.observability import log_event
from app.schemas.zoom_capture import DiscoveryPermit,FetchPermit


class MyNotesError(RuntimeError):pass
class TranscriptNotReady(MyNotesError):pass


class External(BaseModel):
 model_config=ConfigDict(extra='ignore',frozen=True,hide_input_in_errors=True)


class Note(External):
 note_id: str=Field(min_length=1,max_length=200)


class Notes(External):
 notes: tuple[Note,...]=Field(max_length=100)
 @model_validator(mode='after')
 def distinct(self) -> 'Notes':
  if len({note.note_id for note in self.notes})!=len(self.notes):raise ValueError('My Notes returned duplicate note identifiers')
  return self


class Segment(External):
 start_time: str=Field(pattern=r'^\d{2,3}:[0-5]\d:[0-5]\d\.\d{3}$')
 end_time: str=Field(pattern=r'^\d{2,3}:[0-5]\d:[0-5]\d\.\d{3}$')
 speaker_id: str=Field(min_length=1,max_length=128)
 text: str=Field(min_length=1,max_length=2000)


class NoteSpeaker(External):
 speaker_id: str=Field(min_length=1,max_length=128)


def milliseconds(value: str) -> int:
 hour,minute,second=value.split(':')
 seconds,fraction=second.split('.')
 return (int(hour)*3600+int(minute)*60+int(seconds))*1000+int(fraction)


class Transcript(External):
 items: tuple[Segment,...]=Field(min_length=1,max_length=10000)
 speakers: tuple[NoteSpeaker,...]=Field(min_length=1,max_length=100)
 @model_validator(mode='after')
 def consistent(self) -> 'Transcript':
  people={speaker.speaker_id for speaker in self.speakers}
  if len(people)!=len(self.speakers):raise ValueError('My Notes transcript has duplicate speaker identifiers')
  previous=-1
  for segment in self.items:
   start=milliseconds(segment.start_time);end=milliseconds(segment.end_time)
   if end<=start or start<previous:raise ValueError('My Notes transcript requires positive cue duration and ordered start offsets')
   if segment.speaker_id not in people:raise ValueError('My Notes transcript references an unknown speaker')
   previous=start
  return self


class Content(External):
 note_id: str=Field(min_length=1,max_length=200)
 transcript: Transcript|None=Field(default=None)


class MyNotesReader:
 def __init__(self,token: SecretStr) -> None:
  self.http=httpx.Client(base_url='https://api.zoom.us/v2',headers={'Authorization':'Bearer '+token.get_secret_value()},timeout=25,follow_redirects=False)
 def read(self,path: str) -> bytes:
  target=urlsplit(path)
  if target.scheme or target.netloc or '\\' in path or not target.path.startswith('/my_notes/notes'):
   raise PermissionError('My Notes requests must stay on the fixed Zoom API origin and My Notes resource path')
  for attempt in range(1,4):
   try:
    with self.http.stream('GET',path) as response:
     status=response.status_code
     if status==200:
      result=bytearray()
      for part in response.iter_bytes():
       if len(result)+len(part)>1048576:raise MyNotesError('My Notes API content exceeds the one-MiB lab limit; no partial transcript was used')
       result.extend(part)
      return bytes(result)
     if status not in (429,500,502,503,504) or attempt==3:
      raise MyNotesError('My Notes '+target.path+' request failed; HTTP '+str(status)+'. Check approved own-user scopes, note ownership and allowance; no substitute transcript was used.')
     log_event('zoom_my_notes_retry',level=logging.WARNING,status=status,attempt=attempt)
   except (httpx.TimeoutException,httpx.NetworkError) as error:
    if attempt==3:raise MyNotesError('My Notes API network request failed after three attempts; '+type(error).__name__) from None
    log_event('zoom_my_notes_retry',level=logging.WARNING,error_type=type(error).__name__,attempt=attempt)
   time.sleep(attempt)
  raise MyNotesError('My Notes API request ended without a response')
 def notes(self,meeting: DiscoveryPermit) -> Notes:
  return Notes.model_validate_json(self.read('/my_notes/notes?'+urlencode({'meeting_id':meeting.original_uuid})))
 def transcript(self,meeting: FetchPermit,note_id: str) -> Transcript:
  index=self.notes(meeting)
  if not index.notes:raise TranscriptNotReady('The registered Zoom occurrence has no indexed note yet; wait for provider processing and retry')
  if note_id not in {note.note_id for note in index.notes}:raise PermissionError('Note ID is not associated with the registered synthetic meeting; content was not requested')
  content=Content.model_validate_json(self.read('/my_notes/notes/'+quote(note_id,safe='')+'/content?include=transcript'))
  if content.note_id!=note_id:raise MyNotesError('My Notes returned content for a different note identifier')
  if content.transcript is None:raise TranscriptNotReady('The registered My Notes item has no transcript yet; complete capture and check again')
  return content.transcript
 def close(self) -> None:self.http.close()
