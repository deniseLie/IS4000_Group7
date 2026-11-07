"""HMAC-authenticated LangGraph saver fenced by one live meeting event. No auth, source text or model input in state.

SQL retains the queue claim until the final checkpoint/interrupt is durable. A replacement worker can resume the
same thread after a crash, while a superseded run cannot write. Only the closed JSON codec is accepted.
"""
import asyncio,hashlib,hmac,json
from collections.abc import AsyncIterator,Sequence
from uuid import UUID
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver,ChannelVersions,Checkpoint,CheckpointMetadata,CheckpointTuple,WRITES_IDX_MAP
from pydantic import JsonValue,SecretStr,TypeAdapter
from app.services.checkpoint_codec import CheckpointCodec,CheckpointIntegrityError
from app.services.checkpoints import CheckpointPosition,Envelope,SavedWrite,Snapshot
from app.services.meetings.database import WorkerDatabase

NAMESPACE=''


class MeetingCheckpointSaver(BaseCheckpointSaver[int]):
    def __init__(self,database: WorkerDatabase,meeting: UUID,run_id: UUID,key: SecretStr) -> None:
        super().__init__(serde=CheckpointCodec())
        if len(key.get_secret_value())<43:raise CheckpointIntegrityError('Configure a stable private meeting checkpoint signing key of at least 43 characters')
        self.database,self.meeting,self.run_id,self.key=database,meeting,run_id,key

    def position(self,config: RunnableConfig) -> CheckpointPosition:
        position=CheckpointPosition.model_validate(config.get('configurable'))
        if position.thread_id!=self.meeting or position.checkpoint_ns!=NAMESPACE:
            raise CheckpointIntegrityError('Checkpoint must belong to this claimed meeting and lifecycle namespace')
        return position

    def config_for(self,checkpoint_id: str|None) -> RunnableConfig:
        return {'configurable':{'thread_id':str(self.meeting),'checkpoint_ns':NAMESPACE,'checkpoint_id':checkpoint_id}}

    def signature(self,position: CheckpointPosition,identity: list[JsonValue],payload: str) -> str:
        message=json.dumps(['meeting-lifecycle-v1',str(self.meeting),position.checkpoint_ns,position.checkpoint_id,identity,payload],
            ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
        return hmac.new(self.key.get_secret_value().encode(),message,hashlib.sha256).hexdigest()

    def pack(self,position: CheckpointPosition,identity: list[JsonValue],value: object) -> Envelope:
        kind,data=self.serde.dumps_typed(value)
        if kind!='demobank-json-v1':raise CheckpointIntegrityError('Meeting checkpoints require the closed JSON codec')
        payload=data.decode()
        return Envelope(payload=payload,signature=self.signature(position,identity,payload))

    def unpack(self,position: CheckpointPosition,identity: list[JsonValue],data: Envelope) -> object:
        if not hmac.compare_digest(self.signature(position,identity,data.payload),data.signature):
            raise CheckpointIntegrityError('Meeting checkpoint signature is invalid; restore the configured signing key or investigate altered state')
        return self.serde.loads_typed(('demobank-json-v1',data.payload.encode()))

    async def aget_tuple(self,config: RunnableConfig) -> CheckpointTuple|None:
        position=self.position(config)
        value=await asyncio.to_thread(self.database.graph_read,self.meeting,self.run_id,NAMESPACE,position.checkpoint_id)
        if value is None:return None
        saved=Snapshot.model_validate(value)
        if saved.namespace!=NAMESPACE or position.checkpoint_id is not None and saved.checkpoint_id!=position.checkpoint_id:
            raise CheckpointIntegrityError('Database returned another meeting checkpoint position')
        current=position.model_copy(update={'checkpoint_id':saved.checkpoint_id})
        checkpoint,metadata,parent=TypeAdapter(tuple[Checkpoint,CheckpointMetadata,str|None]).validate_python(self.unpack(current,['checkpoint'],saved.data))
        if checkpoint['id']!=saved.checkpoint_id:raise CheckpointIntegrityError('Checkpoint storage and signed payload IDs differ')
        writes=[(item.task_id,item.channel,self.unpack(current,['write',item.task_id,item.task_path,item.write_index,item.channel],item.data)) for item in saved.writes]
        return CheckpointTuple(config=self.config_for(saved.checkpoint_id),checkpoint=checkpoint,metadata=metadata,
            parent_config=self.config_for(parent) if parent is not None else None,pending_writes=writes)

    async def aput(self,config: RunnableConfig,checkpoint: Checkpoint,metadata: CheckpointMetadata,new_versions: ChannelVersions) -> RunnableConfig:
        previous=self.position(config);current=previous.model_copy(update={'checkpoint_id':checkpoint['id']})
        data=self.pack(current,['checkpoint'],(checkpoint,metadata,previous.checkpoint_id))
        await asyncio.to_thread(self.database.graph_save,self.meeting,self.run_id,NAMESPACE,checkpoint['id'],TypeAdapter(JsonValue).validate_python(data.model_dump()))
        return self.config_for(checkpoint['id'])

    async def aput_writes(self,config: RunnableConfig,writes: Sequence[tuple[str,object]],task_id: str,*task_path: str) -> None:
        if len(task_path)>1:raise CheckpointIntegrityError('A pending write accepts one LangGraph task path')
        path=task_path[0] if task_path else '';position=self.position(config)
        if position.checkpoint_id is None:raise CheckpointIntegrityError('Pending writes require a checkpoint ID')
        items=[SavedWrite(task_id=task_id,task_path=path,write_index=WRITES_IDX_MAP.get(channel,index),channel=channel,
            data=self.pack(position,['write',task_id,path,WRITES_IDX_MAP.get(channel,index),channel],
                {'error_type':type(value).__name__} if channel=='__error__' and isinstance(value,BaseException) else value))
            for index,(channel,value) in enumerate(writes)]
        await asyncio.to_thread(self.database.graph_save_writes,self.meeting,self.run_id,NAMESPACE,position.checkpoint_id,
            TypeAdapter(JsonValue).validate_python([item.model_dump() for item in items]))

    async def alist(self,config: RunnableConfig|None,*,filter: dict[str,object]|None,before: RunnableConfig|None,limit: int|None) -> AsyncIterator[CheckpointTuple]:
        if config is None:raise CheckpointIntegrityError('Meeting history requires the claimed meeting thread')
        position=self.position(config);bound=self.position(before) if before is not None else None
        if limit is not None and (isinstance(limit,bool) or not 0<=limit<=100):raise CheckpointIntegrityError('History limit must be between zero and one hundred')
        cursor=self.config_for(position.checkpoint_id);seen: set[str]=set();count=0
        while limit is None or count<limit:
            saved=await self.aget_tuple(cursor)
            if saved is None:return
            key=saved.checkpoint['id']
            if key in seen:raise CheckpointIntegrityError('Meeting checkpoint history contains a parent cycle')
            seen.add(key)
            if (bound is None or bound.checkpoint_id is None or key<bound.checkpoint_id) and (filter is None or all(saved.metadata.get(k)==v for k,v in filter.items())):
                yield saved;count+=1
            if saved.parent_config is None:return
            cursor=saved.parent_config
