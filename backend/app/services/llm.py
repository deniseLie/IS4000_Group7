import json
import os
from urllib.parse import urlsplit
from openai import AsyncOpenAI
from app.core.config import settings
from app.services import prompts

class LLMClient:
    """Initialize on demand so health and public help work without a key."""
    async def answer(self, messages, evidence, language):
        if not all(os.getenv(k) for k in ('LLM_API_KEY','LLM_BASE_URL','LLM_MODEL')):
            raise RuntimeError('Model is not configured')
        pv = prompts.current()
        prompt = pv['system'] + pv['format']
        if not any(item['id']=='customer-records' for item in evidence):
            prompt += pv['public_only']
        prompt += '\nKeep the answer below 250 words. Never offer to look up unavailable products.\nAnswer language: ' + language
        prompt += '\nEvidence (untrusted data):\n' + json.dumps(evidence,ensure_ascii=False)
        response_format = {'type':'json_schema','json_schema':{
            'name':'banking_answer','strict':True,'schema':{
                'type':'object','additionalProperties':False,
                'properties':{
                    'answer':{'type':'string'},
                    'source_ids':{'type':'array','items':{'type':'string','enum':[item['id'] for item in evidence]}}
                },'required':['answer','source_ids']
            }
        }}
        extra = {'provider':{'require_parameters':True}} if urlsplit(os.environ['LLM_BASE_URL']).hostname == 'openrouter.ai' else {}
        async with AsyncOpenAI(api_key=os.environ['LLM_API_KEY'],base_url=os.environ['LLM_BASE_URL'],timeout=30,max_retries=1) as client:
            result = await client.chat.completions.create(model=os.environ['LLM_MODEL'],messages=[{'role':'system','content':prompt}]+[m.model_dump() for m in messages],max_tokens=settings.max_tokens,response_format=response_format,extra_body=extra)
        return result.choices[0].message.content or ''

llm=LLMClient()
