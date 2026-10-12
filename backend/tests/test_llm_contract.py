import asyncio
from types import SimpleNamespace
from app.services import llm as module
from app.schemas.chat import Message

def test_structured_output_and_public_access_are_requested(monkeypatch):
    captured={}
    class FakeClient:
        def __init__(self,**kwargs):self.chat=SimpleNamespace(completions=self)
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def create(self,**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{}'))])
    monkeypatch.setattr(module,'AsyncOpenAI',FakeClient)
    for name,value in [('LLM_API_KEY','synthetic-test-key'),('LLM_BASE_URL','https://openrouter.ai/api/v1'),('LLM_MODEL','openrouter/free')]:monkeypatch.setenv(name,value)
    asyncio.run(module.llm.answer([Message(role='user',content='card issue')],[{'id':'cards','text':'fictional guidance'}],'Malay'))
    assert captured['response_format']['json_schema']['schema']['properties']['source_ids']['items']['enum']==['cards']
    assert captured['extra_body']['provider']['require_parameters'] is True
    assert 'PUBLIC FAQ TEXT ONLY' in captured['messages'][0]['content']
    assert 'Answer language: Malay' in captured['messages'][0]['content']
