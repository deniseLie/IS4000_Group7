"""Async LangGraph saver over the public-key/user-JWT Data API, scoped to one owned turn.

The SQL functions interlock writes with turn completion/cancellation and audit atomically.
Pending writes may arrive before their snapshot, as permitted by LangGraph's execution order.
An HMAC binds every payload to its session, case, namespace, checkpoint and write identity:
public Data API callers cannot forge state the server will restore. Keep the signing key
stable across backend replicas/restarts; changing it deliberately invalidates old snapshots.
"""
import hashlib
import hmac
import json
import os
from collections.abc import AsyncIterator,Sequence
from uuid import UUID
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver,ChannelVersions,Checkpoint,CheckpointMetadata,CheckpointTuple,WRITES_IDX_MAP
from postgrest import SyncPostgrestClient
from pydantic import BaseModel,Field,JsonValue,SecretStr,TypeAdapter
from app.core.data_api import execute
from app.schemas.contracts import RequestContext
from app.schemas.conversations import ConversationAccess,TurnRecord
from app.services.checkpoint_codec import CheckpointCodec,CheckpointIntegrityError
from app.services.conversations import guest_token


class Envelope(BaseModel):
    payload: str=Field(max_length=524288)
    signature: str=Field(pattern=r'^[a-f0-9]{64}$')


class SavedWrite(BaseModel):
    task_id: str=Field(min_length=1,max_length=100)
    task_path: str=Field(max_length=1000)
    write_index: int=Field(strict=True)
    channel: str=Field(min_length=1,max_length=200)
    data: Envelope


class Snapshot(BaseModel):
    checkpoint_id: str=Field(min_length=1,max_length=100)
    namespace: str=Field(max_length=200)
    data: Envelope
    writes: list[SavedWrite]=Field(max_length=100)


class CheckpointPosition(BaseModel):
    thread_id: UUID
    checkpoint_ns: str=Field(default='',max_length=200)
    checkpoint_id: str | None=Field(default=None,min_length=1,max_length=100)


def signing_key() -> SecretStr:
    key=os.environ.get('CHECKPOINT_SIGNING_KEY','')
    if len(key)<43:raise CheckpointIntegrityError('Set a private CHECKPOINT_SIGNING_KEY of at least 43 random characters before enabling saved v2 workflows')
    return SecretStr(key)


class PostgrestCheckpointSaver(BaseCheckpointSaver[int]):
    """Full snapshots only; async graph execution uses no privileged database credentials."""

    def __init__(self,db: SyncPostgrestClient,access: ConversationAccess,ctx: RequestContext,turn: TurnRecord,key: SecretStr) -> None:
        super().__init__(serde=CheckpointCodec())
        if turn.session_id!=access.session_id or turn.case_id!=ctx.case_id or turn.customer_id!=ctx.principal.customer_id:
            raise CheckpointIntegrityError('Workflow saver requires the current owned turn and case')
        if len(key.get_secret_value())<43:raise CheckpointIntegrityError('Checkpoint signing key must contain at least 43 random characters')
        self.db,self.access,self.ctx,self.turn,self.key=db,access,ctx,turn,key

    def position(self,config: RunnableConfig) -> CheckpointPosition:
        position=CheckpointPosition.model_validate(config.get('configurable'))
        if position.thread_id!=self.turn.case_id:raise CheckpointIntegrityError('Checkpoint thread must be the owned conversation case')
        return position

    def config_for(self,namespace: str,checkpoint_id: str) -> RunnableConfig:
        return {'configurable':{'thread_id':str(self.turn.case_id),'checkpoint_ns':namespace,'checkpoint_id':checkpoint_id}}

    def signature(self,position: CheckpointPosition,identity: list[JsonValue],payload: str) -> str:
        scope=[str(self.access.session_id),str(self.turn.case_id),self.turn.customer_id,
            position.checkpoint_ns,position.checkpoint_id,identity,payload]
        message=json.dumps(scope,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
        return hmac.new(self.key.get_secret_value().encode(),message,hashlib.sha256).hexdigest()

    def pack(self,position: CheckpointPosition,identity: list[JsonValue],value: object) -> Envelope:
        kind,data=self.serde.dumps_typed(value)
        if kind!='demobank-json-v1':raise CheckpointIntegrityError('Workflow saver requires the closed JSON codec')
        payload=data.decode()
        return Envelope(payload=payload,signature=self.signature(position,identity,payload))

    def unpack(self,position: CheckpointPosition,identity: list[JsonValue],envelope: Envelope) -> object:
        expected=self.signature(position,identity,envelope.payload)
        if not hmac.compare_digest(expected,envelope.signature):
            raise CheckpointIntegrityError('Workflow signature is invalid; the snapshot changed or the server signing key differs')
        return self.serde.loads_typed(('demobank-json-v1',envelope.payload.encode()))

    def params(self,position: CheckpointPosition) -> dict[str,JsonValue]:
        return {'p_session_id':str(self.access.session_id),'p_case_id':str(self.turn.case_id),
            'p_namespace':position.checkpoint_ns,'p_checkpoint_id':position.checkpoint_id}

    async def aget_tuple(self,config: RunnableConfig) -> CheckpointTuple | None:
        position=self.position(config)
        params=self.params(position)
        if self.ctx.principal.kind=='guest':
            params['p_guest_token']=guest_token(self.access)
            operation='workflow_read_guest'
        else:operation='workflow_read_customer'
        response=await execute(lambda:self.db.rpc(operation,params).execute(),'workflow.read')
        if response.data is None:return None
        saved=Snapshot.model_validate(response.data)
        if saved.namespace!=position.checkpoint_ns or (position.checkpoint_id is not None and saved.checkpoint_id!=position.checkpoint_id):
            raise CheckpointIntegrityError('Database returned a checkpoint outside the requested position')
        current=position.model_copy(update={'checkpoint_id':saved.checkpoint_id})
        checkpoint,metadata,parent=TypeAdapter(tuple[Checkpoint,CheckpointMetadata,str | None]).validate_python(
            self.unpack(current,['checkpoint'],saved.data))
        if checkpoint['id']!=saved.checkpoint_id:raise CheckpointIntegrityError('Checkpoint payload and storage id disagree')
        writes=[(item.task_id,item.channel,self.unpack(current,
            ['write',item.task_id,item.task_path,item.write_index,item.channel],item.data)) for item in saved.writes]
        return CheckpointTuple(config=self.config_for(saved.namespace,saved.checkpoint_id),checkpoint=checkpoint,metadata=metadata,
            parent_config=self.config_for(saved.namespace,parent) if parent is not None else None,pending_writes=writes)

    async def aput(self,config: RunnableConfig,checkpoint: Checkpoint,metadata: CheckpointMetadata,new_versions: ChannelVersions) -> RunnableConfig:
        previous=self.position(config)
        current=previous.model_copy(update={'checkpoint_id':checkpoint['id']})
        envelope=self.pack(current,['checkpoint'],(checkpoint,metadata,previous.checkpoint_id))
        params={**self.params(current),'p_run_id':str(self.turn.run_id),'p_guest_token':guest_token(self.access),
            'p_data':TypeAdapter(JsonValue).validate_python(envelope.model_dump())}
        await execute(lambda:self.db.rpc('workflow_save_checkpoint',params).execute(),'workflow.save')
        return self.config_for(current.checkpoint_ns,checkpoint['id'])

    async def aput_writes(self,config: RunnableConfig,writes: Sequence[tuple[str,object]],task_id: str,*task_path: str) -> None:
        """LangGraph passes one task path; its older three-argument calls use the protocol's empty path."""
        if len(task_path)>1:raise CheckpointIntegrityError('Pending writes accept at most one LangGraph task path')
        path=task_path[0] if task_path else ''
        position=self.position(config)
        if position.checkpoint_id is None:raise CheckpointIntegrityError('Pending writes require a checkpoint id')
        items=[SavedWrite(task_id=task_id,task_path=path,write_index=WRITES_IDX_MAP.get(channel,index),channel=channel,
            data=self.pack(position,['write',task_id,path,WRITES_IDX_MAP.get(channel,index),channel],value))
            for index,(channel,value) in enumerate(writes)]
        params={**self.params(position),'p_run_id':str(self.turn.run_id),'p_guest_token':guest_token(self.access),
            'p_writes':TypeAdapter(JsonValue).validate_python([item.model_dump() for item in items])}
        await execute(lambda:self.db.rpc('workflow_save_writes',params).execute(),'workflow.writes')

    async def alist(self,config: RunnableConfig | None,*,filter: dict[str,object] | None,before: RunnableConfig | None,limit: int | None) -> AsyncIterator[CheckpointTuple]:
        """Read this scope's parent chain; cross-thread/global enumeration is deliberately unsupported."""
        if config is None:raise CheckpointIntegrityError('Workflow history requires an owned thread configuration')
        if limit is not None and (isinstance(limit,bool) or limit<0 or limit>100):raise CheckpointIntegrityError('Workflow history limit must be between 0 and 100')
        position=self.position(config)
        bound=self.position(before) if before is not None else None
        if bound is not None and bound.checkpoint_ns!=position.checkpoint_ns:raise CheckpointIntegrityError('Workflow history boundary must use the same namespace')
        cursor=config
        count=0
        visited: set[str]=set()
        while limit is None or count<limit:
            saved=await self.aget_tuple(cursor)
            if saved is None:return
            checkpoint_id=saved.checkpoint['id']
            if checkpoint_id in visited:raise CheckpointIntegrityError('Workflow history contains a parent cycle')
            visited.add(checkpoint_id)
            if (bound is None or bound.checkpoint_id is None or checkpoint_id<bound.checkpoint_id) and (
                filter is None or all(saved.metadata.get(key)==value for key,value in filter.items())):
                yield saved
                count+=1
            if saved.parent_config is None:return
            cursor=saved.parent_config
