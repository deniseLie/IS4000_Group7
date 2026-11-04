"""Talking points (migration 0108; RM copilot build plan phase 4). The client's side of the pipeline: her ledger actions
(insight.delete, insight.mute and insight.resume run at once with a receipt; insight.share is offered in chat and waits
for her yes), what she sees under Data sharing > What Sophie sees (her points with her own quotes) and the status both
lanes check first. The RM side only ever gets schemas/meetings.TalkingPoint (list values, dates and ids)."""
from typing import Annotated,Literal
from uuid import UUID
from pydantic import AwareDatetime,BaseModel,ConfigDict,Field,StrictBool,StrictInt,StringConstraints
from app.schemas.meetings import TalkingPoint,TalkingStage,TalkingTopic,Timeframe


class InsightPointTarget(BaseModel):
    """Delete one of her talking points (and its quotes)."""
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


class Quote(BaseModel):
    model_config=ConfigDict(extra='ignore')
    quote: Annotated[str,StringConstraints(min_length=1,max_length=200)]
    recorded_at: AwareDatetime


class MyTalkingPoint(TalkingPoint):
    """Her own view of one point: the RM-side fields, plus the hold and her own words (never sent to the RM side)."""
    model_config=ConfigDict(extra='ignore')
    visible_from: AwareDatetime
    pending: StrictBool
    evidence: list[Quote]=Field(default_factory=list,max_length=3)


class Pause(BaseModel):
    model_config=ConfigDict(extra='ignore')
    pause_id: UUID
    until: AwareDatetime


class MyTalkingPoints(BaseModel):
    model_config=ConfigDict(extra='ignore')
    points: list[MyTalkingPoint]=Field(max_length=4)
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
