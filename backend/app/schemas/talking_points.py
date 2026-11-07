"""Talking points (migration 0108; RM copilot build plan phase 4). The client's side of the pipeline: her ledger actions
(insight.delete, insight.mute and insight.resume run at once with a receipt; since 0110 a delete is undone within 7 days
by insight.restore; insight.share is offered in chat and waits for her yes), what she sees under Data sharing > Topics from your chats (her points as list values; since 0142 no words are kept)
and the status both lanes check first. The RM side only ever gets schemas/meetings.TalkingPoint (list values, dates and ids)."""
from typing import Annotated,Literal
from uuid import UUID
from pydantic import AwareDatetime,BaseModel,ConfigDict,Field,StrictBool,StrictInt,StringConstraints
from app.schemas.meetings import TalkingPoint,TalkingStage,TalkingTopic,Timeframe


class InsightPointTarget(BaseModel):
    """Delete one of her talking points."""
    model_config=ConfigDict(extra='ignore')
    point_id: UUID


class StoredInsightPointTarget(InsightPointTarget):
    topic: TalkingTopic


class InsightTopicTarget(BaseModel):
    """Don't suggest this topic again."""
    model_config=ConfigDict(extra='ignore')
    topic: TalkingTopic


class InsightResumeTarget(BaseModel):
    """Suggest again: no topic ends the pause; a topic undoes its mute."""
    model_config=ConfigDict(extra='ignore')
    topic: TalkingTopic | None=None


class InsightShareTarget(BaseModel):
    """Let her RM know (lane 2): only list values, set by code from her message, never by a model."""
    model_config=ConfigDict(extra='ignore')
    topic: TalkingTopic
    stage: TalkingStage
    timeframe: Timeframe='unknown'


class StoredInsightShareTarget(InsightShareTarget):
    advisor_id: UUID


class InsightDeleteResult(BaseModel):
    model_config=ConfigDict(extra='ignore')
    point_id: UUID
    topic: TalkingTopic
    status: Literal['deleted']
    restore_until: AwareDatetime | None=None  # 0110: Undo until then; purged after


class InsightRestoreResult(BaseModel):
    model_config=ConfigDict(extra='ignore')
    point_id: UUID
    topic: TalkingTopic
    status: Literal['restored']


class InsightMuteResult(BaseModel):
    model_config=ConfigDict(extra='ignore')
    topic: TalkingTopic
    status: Literal['muted']
    removed: StrictInt=Field(ge=0)


class InsightResumeResult(BaseModel):
    model_config=ConfigDict(extra='ignore')
    topic: TalkingTopic | None
    status: Literal['resumed']


class InsightShareResult(BaseModel):
    model_config=ConfigDict(extra='ignore')
    point_id: UUID
    topic: TalkingTopic
    stage: TalkingStage
    timeframe: Timeframe
    origin: Literal['confirmed']
    notified: StrictBool


class MyTalkingPoint(TalkingPoint):
    """Her own view of one point: the RM-side fields plus the 24-hour hold. Never her words (0142)."""
    model_config=ConfigDict(extra='ignore')
    visible_from: AwareDatetime
    pending: StrictBool


class Pause(BaseModel):
    model_config=ConfigDict(extra='ignore')
    pause_id: UUID
    until: AwareDatetime


class RemovedTalkingPoint(TalkingPoint):
    """A point she removed (or that went when she withdrew or narrowed chat topics, or muted it), until it is purged 7
    days later (0110). Undo only for her own Delete."""
    model_config=ConfigDict(extra='ignore')
    removed_at: AwareDatetime
    reason: Literal['client','muted','chat_topics_revoked','scope_narrowed']
    restore_until: AwareDatetime | None
    can_restore: StrictBool


class MyTalkingPoints(BaseModel):
    model_config=ConfigDict(extra='ignore')
    points: list[MyTalkingPoint]=Field(max_length=4)
    removed: list[RemovedTalkingPoint]=Field(default_factory=list,max_length=4)
    muted: list[TalkingTopic]
    pause: Pause | None
    chat_topics: StrictBool
    rm_first_name: Annotated[str,StringConstraints(max_length=80)] | None=None


class TopicStatus(BaseModel):
    """What both lanes read before doing anything (customer_topic_status)."""
    model_config=ConfigDict(extra='ignore')
    chat_topics: StrictBool
    paused: StrictBool
    has_rm: StrictBool
    rm_first_name: Annotated[str,StringConstraints(max_length=80)] | None=None
    muted: list[TalkingTopic]
    prompted: list[TalkingTopic]
    meeting_topics: list[str]
