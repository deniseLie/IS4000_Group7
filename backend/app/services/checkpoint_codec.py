"""Closed JSON checkpoint format: no pickle, imports, arbitrary constructors or credentials.

Only explicitly registered state models can be restored. Envelopes are authenticated by the
connector before decoding; this codec independently rejects secret-bearing state on both paths.
"""
import json
import math
import os
import re
from datetime import datetime
from uuid import UUID
from langgraph.types import Interrupt,Send
from pydantic import BaseModel,JsonValue,TypeAdapter
from app.schemas.agents import Evidence,PlannedIssue
from app.schemas.chat import ChatRequest,Message
from app.schemas.contracts import AgentResult,RiskAssessment,Task,TaskPlan
from app.schemas.events import AnswerEvent,AnswerSection,Source
from app.services.privacy import redact

MODELS: dict[str,type[BaseModel]]={model.__name__:model for model in (
    ChatRequest,Message,RiskAssessment,Task,TaskPlan,AgentResult,Evidence,PlannedIssue,AnswerEvent,AnswerSection,Source)}
PRIVATE_KEYS={'jwt','authorization','api_key','guest_token','service_role_key','checkpoint_signing_key'}
TOKEN=re.compile(r'\b(?:eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|sb_secret_[A-Za-z0-9_-]+|sk-or-v1-[A-Za-z0-9_-]+)\b')
UUID_TEXT=re.compile(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')
MAX_BYTES=524288


class CheckpointIntegrityError(ValueError):
    """A checkpoint is unsafe, out of scope, malformed or unauthenticated."""


def check_text(value: str) -> str:
    private=[os.environ.get(name,'') for name in ('LLM_API_KEY','SUPABASE_SERVICE_ROLE_KEY','CHECKPOINT_SIGNING_KEY')]
    if TOKEN.search(value) or any(len(key)>=12 and key in value for key in private):
        raise CheckpointIntegrityError('Checkpoint state contains a credential; keep authentication outside saved state')
    # Canonical UUID identifiers can contain digit/hyphen runs that resemble card numbers.
    if UUID_TEXT.fullmatch(value):return value
    if redact(value)[1]:
        raise CheckpointIntegrityError('Checkpoint state contains private data; redact it before saving')
    return value


def encode(value: object) -> JsonValue:
    if value is None or isinstance(value,(bool,int)):return value
    if isinstance(value,float):
        if not math.isfinite(value):raise CheckpointIntegrityError('Checkpoint numbers must be finite')
        return value
    if isinstance(value,str):return check_text(value)
    if isinstance(value,UUID):return {'type':'uuid','value':str(value)}
    if isinstance(value,datetime):return {'type':'datetime','value':value.isoformat()}
    if isinstance(value,BaseModel):
        if type(value) not in MODELS.values():raise CheckpointIntegrityError('Checkpoint model is not registered for storage')
        return {'type':type(value).__name__,'value':encode(value.model_dump(mode='json'))}
    if isinstance(value,Interrupt):return {'type':'interrupt','value':encode(value.value),'id':value.id}
    if isinstance(value,Send):return {'type':'send','node':check_text(value.node),'value':encode(value.arg)}
    if isinstance(value,(list,tuple)):return {'type':'tuple' if isinstance(value,tuple) else 'list','value':[encode(item) for item in value]}
    if isinstance(value,dict):
        pairs: list[JsonValue]=[]
        for key,item in value.items():
            if not isinstance(key,str) or key.lower() in PRIVATE_KEYS:raise CheckpointIntegrityError('Checkpoint keys must be public state names')
            pairs.append([check_text(key),encode(item)])
        return {'type':'dict','value':pairs}
    raise CheckpointIntegrityError('Checkpoint state contains an unsupported type')


def decode(value: JsonValue) -> object:
    if not isinstance(value,dict):
        if value is None or isinstance(value,(str,int,float,bool)):return encode(value)
        raise CheckpointIntegrityError('Checkpoint collection requires a type tag')
    kind=value.get('type')
    body=value.get('value')
    if not isinstance(kind,str):raise CheckpointIntegrityError('Checkpoint type tag must be text')
    if kind=='uuid' and isinstance(body,str):return UUID(body)
    if kind=='datetime' and isinstance(body,str):return datetime.fromisoformat(body)
    if kind in MODELS and isinstance(body,dict):
        return MODELS[kind].model_validate(decode(body))
    if kind=='interrupt' and isinstance(value.get('id'),str):return Interrupt(value=decode(body),id=value['id'])
    if kind=='send' and isinstance(value.get('node'),str):return Send(value['node'],decode(body))
    if kind in ('tuple','list') and isinstance(body,list):
        items=[decode(item) for item in body]
        return tuple(items) if kind=='tuple' else items
    if kind=='dict' and isinstance(body,list):
        result: dict[str,object]={}
        for pair in body:
            if not isinstance(pair,list) or len(pair)!=2 or not isinstance(pair[0],str) or pair[0] in result:
                raise CheckpointIntegrityError('Checkpoint object keys are malformed or duplicated')
            result[pair[0]]=decode(pair[1])
        return result
    raise CheckpointIntegrityError('Checkpoint type tag is not recognised')


class CheckpointCodec:
    """LangGraph serializer interface using the closed schema above."""

    def dumps_typed(self,value: object) -> tuple[str,bytes]:
        data=json.dumps(encode(value),ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
        if len(data)>MAX_BYTES:raise CheckpointIntegrityError('Checkpoint exceeds the 512 KiB storage limit')
        return 'demobank-json-v1',data

    def loads_typed(self,value: tuple[str,bytes]) -> object:
        kind,data=value
        if kind!='demobank-json-v1' or len(data)>MAX_BYTES:raise CheckpointIntegrityError('Checkpoint format or size is invalid')
        result=decode(TypeAdapter(JsonValue).validate_json(data))
        encode(result)
        return result
