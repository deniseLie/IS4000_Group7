"""Resume completed model chunks by source hash and prompt version under a live fenced worker claim."""
import asyncio
from app.core.config import settings
from app.core.observability import log_event
from app.schemas.meeting_lifecycle import Claim,JobContext,MinutesContent
from app.services.llm_gateway import LLMBudgetExceeded
from app.services.meetings.database import WorkerDatabase
from app.services.meetings.writer import PROMPT,chunks,merge,propose
from app.services.prompts import load


async def generate_queued(context: JobContext,database: WorkerDatabase,claim: Claim) -> MinutesContent:
    pieces: list[MinutesContent]=[];spent=0;prompt=load('minutes_writer',PROMPT)['id']
    for index,cues in enumerate(chunks(context.cues)):
        await asyncio.to_thread(database.renew,claim)
        ids=tuple(cue.cue_id for cue in cues)
        slot=await asyncio.to_thread(database.chunk,claim,index,prompt,ids)
        if slot.content is not None:
            pieces.append(slot.content);spent+=slot.charged_tokens
            log_event('meeting_chunk_reused',event_id=claim.event_id,chunk_index=index)
        else:
            if spent>=settings.meeting_model_token_budget:raise LLMBudgetExceeded('Meeting model token budget reached before the next uncached chunk')
            piece=await propose(context.model_copy(update={'cues':cues}))
            await asyncio.to_thread(database.save_chunk,claim,context,index,prompt,slot.chunk_hash,ids,piece.content,piece.input_tokens,piece.output_tokens,piece.charged_tokens)
            pieces.append(piece.content);spent+=piece.charged_tokens
            log_event('meeting_chunk_saved',event_id=claim.event_id,chunk_index=index,input_tokens=piece.input_tokens,output_tokens=piece.output_tokens)
        if spent>settings.meeting_model_token_budget:raise LLMBudgetExceeded('Meeting model token budget exceeded; completed chunks are retained for reviewed recovery')
    await asyncio.to_thread(database.renew,claim)
    merged=await merge(tuple(pieces),context)
    if spent+merged.charged_tokens>settings.meeting_model_token_budget:raise LLMBudgetExceeded('Meeting model token budget exceeded during overview selection')
    log_event('meeting_generation_completed',event_id=claim.event_id,chunk_count=len(pieces),charged_tokens=spent+merged.charged_tokens)
    return merged.content
