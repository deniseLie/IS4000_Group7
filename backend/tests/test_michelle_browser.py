"""Michelle's normal UI over native JWT/RLS/real file bytes; optional actual model calls."""
import os
from datetime import date
from pathlib import Path
from uuid import uuid4
import pytest
from pydantic import SecretStr
from app.core.config import settings
from tests.browser_support import NativeBrowserSession,run_browser
from tests.data_api_support import mint_token
from tests.database_support import persona_id
from tests.storage_support import local_storage,template_pdf
from tests.test_notifications import worker_uri

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER_LOCAL')!='1',reason='Opt in to actual native browser integration')


class MichelleSession(NativeBrowserSession):
    missing_file: str
    valid_file: str
    live: bool=False


def test_michelle_upload_check_correct_submit_and_reload(local_data_api,native_api_client,tmp_path,monkeypatch,worker_uri):
    import psycopg
    if os.getenv('TEST_MICHELLE_TRANSPORT')=='codex':monkeypatch.setenv('CHAT_TRANSPORT','codex')
    monkeypatch.setattr(settings,'pipeline','v2');monkeypatch.setattr(settings,'conversation_store','database')
    monkeypatch.setattr(settings,'run_worker_in_api',True);monkeypatch.setattr(settings,'worker_database_url',worker_uri)
    monkeypatch.setattr(settings,'worker_poll_seconds',.2)
    monkeypatch.setenv('CHECKPOINT_SIGNING_KEY','native-michelle-browser-private-checkpoint-signing-key')
    with psycopg.connect(local_data_api.database_uri) as db:
        key=db.execute("select secret from compliance.precheck_keys where key_id='v1'").fetchone()[0]
    monkeypatch.setattr(settings,'document_precheck_signing_key',SecretStr(key));monkeypatch.setattr(settings,'precheck_mode','notice')
    base=['Utility bill','Full name: Synthetic Demo','Document date: '+date.today().isoformat()]
    missing=tmp_path/'Missing Address DEMO.pdf';missing.write_bytes(template_pdf(base))
    valid=tmp_path/'Complete Address DEMO.pdf';valid.write_bytes(template_pdf([*base,'Address: Fictional Lane']))
    with local_storage(local_data_api,tmp_path/'blobs') as url:
        monkeypatch.setenv('SUPABASE_URL',url);monkeypatch.setenv('SUPABASE_ANON_KEY','native-public-test-key')
        case=MichelleSession(data_api_url=url,access_token=mint_token(local_data_api,1002,'aal1',uuid4()),user_id=persona_id(1002),
            missing_file=str(missing),valid_file=str(valid),live=os.getenv('TEST_MICHELLE_LIVE')=='1')
        run_browser(native_api_client,case,'michelle corrects and submits documents',tmp_path,timeout=600)
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute("select submission_status from compliance.requested_items where item_id='00000000-0000-4000-8000-100200000011'").fetchone()==('submitted_awaiting_review',)
        assert db.execute('select count(*) from compliance.prechecks').fetchone()==(2,)
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)


def test_expired_michelle_upload_is_rechecked_in_normal_ui(local_data_api,native_api_client,tmp_path,monkeypatch):
    import hashlib
    if os.getenv('TEST_MICHELLE_TRANSPORT')=='codex':monkeypatch.setenv('CHAT_TRANSPORT','codex')
    import httpx
    import psycopg
    from app.schemas.contracts import ActionCommand
    from tests.test_actions_api import context,headers,owned_case,propose
    from tests.test_document_uploads import upload_target
    monkeypatch.setattr(settings,'pipeline','v2');monkeypatch.setattr(settings,'conversation_store','database')
    monkeypatch.setenv('CHECKPOINT_SIGNING_KEY','native-expired-michelle-private-checkpoint-signing-key')
    ctx=owned_case(local_data_api,context(local_data_api,1002));target=upload_target(local_data_api,1002)
    payload=template_pdf(['Utility bill','Full name: Synthetic Demo','Address: Fictional Lane','Document date: '+date.today().isoformat()])
    target.update(file_name='Freshness DEMO.pdf',mime_type='application/pdf',size_bytes=len(payload))
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute("update compliance.requested_items set precheck_rule_id='proof_address',label='Proof of address',instructions='A utility bill dated within the last 3 months, showing your full name and address.' where item_id=%s",(target['item_id'],))
        key=db.execute("select secret from compliance.precheck_keys where key_id='v1'").fetchone()[0]
    monkeypatch.setattr(settings,'document_precheck_signing_key',SecretStr(key));monkeypatch.setattr(settings,'precheck_mode','notice')
    with local_storage(local_data_api,tmp_path/'blobs') as url:
        monkeypatch.setenv('SUPABASE_URL',url);monkeypatch.setenv('SUPABASE_ANON_KEY','native-public-test-key')
        uploaded=httpx.post(url+f"/storage/v1/object/demobank-compliance-private/1002/{target['case_id']}/{target['document_id']}",headers={**headers(ctx),'Content-Type':'application/pdf'},content=payload)
        assert uploaded.status_code==200
        propose(native_api_client,ctx,ActionCommand(type='document.register',idempotency_key=f'{ctx.case_id}:register:document.register',target=target))
        result=native_api_client.post(f"/documents/{target['document_id']}/precheck",headers=headers(ctx));assert result.status_code==200,result.text
        assert result.json()['data']['status']=='no_issues'
        with psycopg.connect(local_data_api.database_uri) as db:
            db.execute("update compliance.prechecks set report=jsonb_set(jsonb_set(report,'{checked_at}',to_jsonb(clock_timestamp()-interval '2 days')),'{expires_at}',to_jsonb(clock_timestamp()-interval '1 day')) where document_id=%s",(target['document_id'],))
        case=NativeBrowserSession(data_api_url=url,access_token=ctx.principal.jwt.get_secret_value(),user_id=ctx.principal.user_id)
        run_browser(native_api_client,case,'rechecks expired Michelle upload',tmp_path)
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select count(*) from compliance.prechecks').fetchone()==(2,)
        assert db.execute('select submission_status from compliance.requested_items where item_id=%s',(target['item_id'],)).fetchone()==('submitted_awaiting_review',)
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)
