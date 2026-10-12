import asyncio
from app.services import llm as module
from app.schemas.chat import Message

def test_structured_output_and_public_access_are_requested(monkeypatch):
    captured={}
    class FakeModel:
        def with_structured_output(self,schema,**kwargs):
            captured['schema']=schema;captured['so_kwargs']=kwargs
            return self
        async def ainvoke(self,messages):
            captured['messages']=messages
            return {'answer':'ok','source_ids':['cards']}
    def fake_init(model,**kwargs):
        captured['model']=model;captured['init_kwargs']=kwargs
        return FakeModel()
    monkeypatch.setattr(module,'init_chat_model',fake_init)
    for name,value in [('LLM_API_KEY','synthetic-test-key'),('LLM_BASE_URL','https://openrouter.ai/api/v1'),('LLM_MODEL','openrouter/free')]:monkeypatch.setenv(name,value)
    monkeypatch.delenv('LLM_PROVIDER',raising=False)
    out=asyncio.run(module.llm.answer([Message(role='user',content='card issue')],[{'id':'cards','text':'fictional guidance'}],'Malay'))
    assert captured['schema']['properties']['source_ids']['items']['enum']==['cards']
    assert captured['so_kwargs']=={'method':'json_schema','strict':True}
    assert captured['init_kwargs']['model_provider']=='openai'
    assert captured['init_kwargs']['extra_body']['provider']['require_parameters'] is True
    assert captured['messages'][0][0]=='system'
    assert 'PUBLIC FAQ TEXT ONLY' in captured['messages'][0][1]
    assert 'Answer language: Malay' in captured['messages'][0][1]
    assert '"source_ids": ["cards"]' in out
