"""Lane 1 v2 (migration 0143): what the state-aware classifier reads before a turn (customer_topic_state). Her points
cross as list values only: no ids, dates or words, and extra fields are refused, so nothing else can reach the model."""
from pydantic import BaseModel,ConfigDict,Field,StrictBool
from app.schemas.meetings import ConcernTag,TalkingKind,TalkingOrigin,TalkingStage,TalkingTopic,Timeframe
from app.schemas.talking_points import TopicStatus


class StatePoint(BaseModel):
    model_config=ConfigDict(extra='forbid')
    topic: TalkingTopic
    stage: TalkingStage
    timeframe: Timeframe
    kind: TalkingKind | None=None
    concerns: list[ConcernTag]=Field(default_factory=list,max_length=4)
    origin: TalkingOrigin
    on_hold: StrictBool


class TopicState(TopicStatus):
    """TopicStatus (what both lanes check first), her current points and the topics active in this chat (recorded from
    one of her last 4 turns in it)."""
    points: list[StatePoint]=Field(max_length=4)
    active_topics: list[TalkingTopic]
