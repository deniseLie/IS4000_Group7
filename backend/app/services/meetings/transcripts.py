"""Bounded imports of user-supplied Zoom exports; speaker roles are RM assertions, not voice authentication."""
from html import unescape
from hashlib import sha256
import json
import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel,ConfigDict,Field,model_validator
from app.services.privacy import redact_details
from app.schemas.meeting_lifecycle import Cue,Model

class Speaker(Model):
    label: str=Field(min_length=1,max_length=80)
    role: Literal['rm','client']

class ImportRequest(Model):
    revision: int=Field(strict=True,ge=1)
    filename: str=Field(min_length=1,max_length=160)
    text: str=Field(min_length=1,max_length=1048576)
    speakers: tuple[Speaker,...]=Field(max_length=10)
    coverage: Literal['full_rm_attested','partial','unknown']
    command_id: UUID

    @model_validator(mode='after')
    def distinct_labels(self) -> 'ImportRequest':
        labels=[s.label.casefold().strip() for s in self.speakers]
        if len(labels)!=len(set(labels)):raise ValueError('Speaker labels must be distinct')
        return self

class ParsedCue(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True)
    start_seconds: int|None
    label: str|None
    text: str

class Imported(Model):
    origin: Literal['zoom_export_vtt','zoom_export_txt']
    file_hash: str
    cues: tuple[Cue,...]
    coverage: Literal['full_rm_attested','partial','unknown']
    redaction_count: int


def seconds(value: str) -> int:
    match=re.fullmatch(r'(?:(\d{2,3}):)?([0-5]\d):([0-5]\d)(?:[.,](\d{3}))?',value)
    if match is None:raise ValueError('Transcript time must be HH:MM:SS.mmm or MM:SS.mmm with valid minutes and seconds')
    return int(match[1] or 0)*3600+int(match[2])*60+int(match[3])


def labelled(text: str) -> tuple[str|None,str]:
    voice=re.fullmatch(r'<v(?:\.[\w.-]+)?\s+([^>]{1,80})>(.*?)(?:</v>)?',text,flags=re.S)
    if voice is not None:return unescape(voice[1]).strip(),unescape(voice[2]).strip()
    plain=re.fullmatch(r'([^:\n<>]{1,80}):\s+(.+)',text,flags=re.S)
    if plain is not None:return plain[1].strip(),plain[2].strip()
    return None,text.strip()


def parse_vtt(text: str) -> tuple[ParsedCue,...]:
    normalized=text.lstrip('\ufeff').replace('\r\n','\n').replace('\r','\n')
    if not normalized.startswith('WEBVTT\n'):raise ValueError('A .vtt upload must begin with WEBVTT')
    result=[]
    previous=-1
    for block in re.split(r'\n[ \t]*\n',normalized.strip())[1:]:
        lines=block.splitlines()
        if not lines:continue
        if lines[0]=='NOTE' or lines[0].startswith('NOTE '):continue
        index=next((i for i,line in enumerate(lines) if '-->' in line),None)
        if index is None:raise ValueError('VTT cue has no valid time range; unsupported STYLE/REGION blocks must be removed explicitly')
        timing=re.fullmatch(r'(\S+)\s+-->\s+(\S+)(?:\s+.*)?',lines[index])
        if timing is None:raise ValueError('VTT cue has a malformed time range')
        start,end=seconds(timing[1]),seconds(timing[2])
        # Millisecond ordering is validated separately to preserve subsecond speech cues.
        def millis(value: str) -> int:
            parts=value.replace(',','.').split('.')
            return seconds(value)*1000+int(parts[1] if len(parts)==2 else '0')
        if millis(timing[2])<=millis(timing[1]) or millis(timing[1])<previous:raise ValueError('VTT cues must have positive duration and nondecreasing start times')
        words=' '.join(lines[index+1:]).strip();label,words=labelled(words)
        if not words:raise ValueError('VTT contains an empty speech cue')
        if re.search(r'<[^>]+>',words):raise ValueError('Unsupported embedded markup in VTT speech; review/export plain text instead')
        result.append(ParsedCue(start_seconds=start,label=label,text=unescape(words)));previous=millis(timing[1])
    if not result:raise ValueError('Transcript contains no speech cues')
    return tuple(result)


def parse_txt(text: str) -> tuple[ParsedCue,...]:
    normalized=text.lstrip('\ufeff').replace('\r\n','\n').replace('\r','\n').strip()
    result=[]
    blocks: list[list[str]]=[]
    for line in normalized.splitlines():
        if not line.strip():
            if blocks and blocks[-1]:blocks.append([])
            continue
        starts_speech=re.match(r'(?:\d{2,3}:\d{2}:\d{2}(?:[.,]\d{3})?\s+)?[^:\n<>]{1,80}:\s+',line)
        if not blocks or (blocks[-1] and starts_speech is not None):blocks.append([])
        blocks[-1].append(line)
    for lines in blocks:
        if not lines:continue
        match=re.fullmatch(r'(\d{2,3}:\d{2}:\d{2}(?:[.,]\d{3})?)\s+(.+)',lines[0])
        if match is not None:
            label,words=labelled(' '.join([match[2],*lines[1:]]));start=seconds(match[1])
        else:
            label,words=labelled(' '.join(lines));start=None
        if not words.strip():raise ValueError('TXT contains an empty speech block')
        if '\x00' in words:raise ValueError('Transcript contains binary NUL bytes')
        result.append(ParsedCue(start_seconds=start,label=label,text=words))
    if not result:raise ValueError('Transcript contains no speech blocks')
    return tuple(result)


def imported(body: ImportRequest) -> Imported:
    raw=body.text.encode('utf-8')
    if len(raw)>1048576:raise ValueError('Transcript exceeds the 1 MiB upload limit')
    if '/' in body.filename or '\\' in body.filename:raise ValueError('Use a filename, not a file path')
    suffix=body.filename.rsplit('.',1)[-1].lower()
    if suffix=='vtt':parsed=parse_vtt(body.text);origin='zoom_export_vtt'
    elif suffix=='txt':parsed=parse_txt(body.text);origin='zoom_export_txt'
    else:raise ValueError('Only Zoom .vtt or .txt exports are supported; enter RM notes separately')
    if len(parsed)>100:raise ValueError('The meeting intake supports at most 100 speech cues; no text was truncated')
    aliases={s.label.casefold().strip():s.role for s in body.speakers};cues=[];count=0
    for index,item in enumerate(parsed,1):
        safe,changes=redact_details(item.text);count+=sum(change.count for change in changes)
        cues.append(Cue(cue_id='cue'+str(index),speaker=aliases.get((item.label or '').casefold().strip(),'unmapped'),start_seconds=item.start_seconds,text=safe))
    if len(json.dumps([c.model_dump(mode='json') for c in cues]).encode())>16000:
        raise ValueError('Redacted transcript exceeds the 16 KB evidence limit of the meeting intake; no text was truncated')
    return Imported(origin=origin,file_hash=sha256(raw).hexdigest(),cues=tuple(cues),coverage=body.coverage,redaction_count=count)
