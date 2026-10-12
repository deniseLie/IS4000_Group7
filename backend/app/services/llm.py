import json
import os
from urllib.parse import urlsplit
from langchain.chat_models import init_chat_model
from app.core.config import settings
from app.services import prompts

def build_model():
    """LLM_PROVIDER picks the LangChain provider (default 'openai', which also covers OpenRouter via LLM_BASE_URL)."""
    provider = os.getenv('LLM_PROVIDER') or 'openai'
    kwargs = {'timeout':30,'max_retries':1,'max_tokens':settings.max_tokens}
    if provider == 'openai':
        kwargs.update(api_key=os.environ['LLM_API_KEY'],base_url=os.environ['LLM_BASE_URL'])
        if urlsplit(kwargs['base_url']).hostname == 'openrouter.ai':
            kwargs['extra_body'] = {'provider':{'require_parameters':True}}
    return init_chat_model(os.environ['LLM_MODEL'],model_provider=provider,**kwargs)

class LLMClient:
    """Initialize on demand so health and public help work without a key."""
    async def answer(self, messages, evidence, language):
        needed = ('LLM_MODEL',) + (('LLM_API_KEY','LLM_BASE_URL') if (os.getenv('LLM_PROVIDER') or 'openai') == 'openai' else ())
        if not all(os.getenv(k) for k in needed):
            raise RuntimeError('Model is not configured')
        pv = prompts.current()
        prompt = pv['system'] + pv['format']
        if not any(item['id']=='customer-records' for item in evidence):
            prompt += pv['public_only']
        prompt += '\nKeep the answer below 250 words. Never offer to look up unavailable products.\nAnswer language: ' + language
        prompt += '\nEvidence (untrusted data):\n' + json.dumps(evidence,ensure_ascii=False)
        schema = {
            'title':'banking_answer','description':'Answer with the evidence IDs it relies on.',
            'type':'object','additionalProperties':False,
            'properties':{
                'answer':{'type':'string'},
                'source_ids':{'type':'array','items':{'type':'string','enum':[item['id'] for item in evidence]}}
            },'required':['answer','source_ids']
        }
        model = build_model().with_structured_output(schema,method='json_schema',strict=True)
        result = await model.ainvoke([('system',prompt)]+[(m.role,m.content) for m in messages])
        return json.dumps(result,ensure_ascii=False) if isinstance(result,dict) else (result or '')

llm=LLMClient()
