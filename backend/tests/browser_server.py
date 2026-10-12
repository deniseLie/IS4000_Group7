"""Test-only server: real HTTP API with deterministic model responses."""
import asyncio
import os
os.environ['TRACE_STORE']='0'
import json
from app.main import app
from app.services.llm import llm
async def answer(messages,evidence,language):
    question=messages[-1].content
    if 'simulate failure' in question:raise RuntimeError('simulated provider outage')
    if 'slow response' in question:await asyncio.sleep(2)
    if 'invented fee test' in question:
        return json.dumps({'answer':'Your annual fee is SGD 199.', 'source_ids':['cards']})
    item=evidence[0]
    return json.dumps({'answer':item['text'],'source_ids':[item['id']]})
llm.answer=answer
