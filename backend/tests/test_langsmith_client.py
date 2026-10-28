import langsmith
from langchain_core.messages import HumanMessage
from langchain_core.tracers.langchain import get_client
from app.core import langsmith_client

CARD='my card 4111 1111 1111 1111, email alex@example.com'
GONE='[private detail removed]'

def test_scrub_redacts_every_string_and_keeps_shape():
    data={'question':CARD,'history':[{'role':'user','content':'pin 1234'}],'count':3,'ok':True,'none':None}
    assert langsmith_client.scrub(data)=={'question':f'my card {GONE}, email {GONE}','history':[{'role':'user','content':GONE}],'count':3,'ok':True,'none':None}

def test_scrub_keeps_ordinary_text():
    for text in ('How do I reset my password?','如何查看我的银行对账单？','Transfer SGD 1,000 on 12 March'):
        assert langsmith_client.scrub({'q':text})=={'q':text}

def test_client_redacts_run_inputs_outputs_metadata_and_errors():
    c=langsmith_client.client(api_key='test',auto_batch_tracing=False)
    # LangChain messages reach the anonymizer already serialised to JSON.
    inputs=c._hide_run_inputs({'messages':[[HumanMessage(CARD)]]})
    assert inputs['messages'][0][0]['content']==f'my card {GONE}, email {GONE}'
    assert c._hide_run_outputs({'answer':'Your OTP is 123456'})=={'answer':f'Your {GONE}'}
    assert c._hide_run_metadata({'note':'alex@example.com','case':'statement'})=={'note':GONE,'case':'statement'}
    assert c._hide_run_error('ValueError: cvv 123')==f'ValueError: {GONE}'

def test_install_sets_the_client_langchain_traces_through():
    try:
        c=langsmith_client.install(api_key='test',auto_batch_tracing=False)
        assert get_client() is c
    finally:langsmith.configure(client=None)

def test_app_installs_client_only_when_tracing_is_on(monkeypatch):
    built=[]
    monkeypatch.setattr(langsmith_client,'install',lambda:built.append(1) or 'client')
    with langsmith.tracing_context(enabled=False):
        assert langsmith_client.install_if_tracing() is None
    with langsmith.tracing_context(enabled=True):
        assert langsmith_client.install_if_tracing()=='client'
    assert built==[1]
