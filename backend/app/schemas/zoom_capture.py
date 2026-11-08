"""Closed runtime contracts for this meeting's provider retrieval and redacted evidence."""
from typing import Literal
from uuid import UUID
from pydantic import Field
from app.schemas.meeting_lifecycle import Model,Cue,Hash

class DiscoveryPermit(Model):
    meeting_id: UUID
    provider_id: int=Field(strict=True,gt=0)
    account_id: str=Field(min_length=1,max_length=128)
    host_id: str=Field(min_length=1,max_length=128)
    original_uuid: str=Field(min_length=1,max_length=200)
    revision: int=Field(strict=True,gt=0)
    consent_epoch: int=Field(strict=True,gt=0)
    source_exists: bool

class FetchPermit(DiscoveryPermit):
    job_id: UUID
    note_id: str=Field(min_length=1,max_length=200)

class SegmentPosition(Model):
    cue_id: str
    speaker_key: str=Field(pattern=r'^speaker[1-9][0-9]{0,2}$')
    start_ms: int=Field(strict=True,ge=0)
    end_ms: int=Field(strict=True,gt=0)

class FetchedEvidence(Model):
    cues: tuple[Cue,...]=Field(min_length=1,max_length=1000)
    segments: tuple[SegmentPosition,...]=Field(min_length=1,max_length=1000)
    transcript_hash: Hash

class SpeakerMapping(Model):
    source_id: UUID
    source_hash: Hash
    revision: int=Field(strict=True,gt=0)
    consent_epoch: int=Field(strict=True,gt=0)
    mapping: dict[str,Literal['rm','client','unmapped']]=Field(max_length=100)
    command_id: UUID

class ZoomEvidence(Model):
    source_id: UUID
    source_hash: Hash
    revision: int=Field(strict=True,gt=0)
    consent_epoch: int=Field(strict=True,gt=0)
    cues: tuple[Cue,...]=Field(min_length=1,max_length=1000)
    segments: tuple[SegmentPosition,...]=Field(min_length=1,max_length=1000)
    mapping: dict[str,Literal['rm','client','unmapped']]
    coverage: Literal['unknown']
