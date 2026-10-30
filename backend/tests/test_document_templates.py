"""Template document checks: synthetic documents only (made-up names, addresses and account numbers)."""
import os
from datetime import date,datetime
from uuid import uuid4
from zoneinfo import ZoneInfo
import pytest
from app.services import document_templates as templates

TODAY=date(2026,10,28)
NAME='Michelle Ng (DEMO)'
BILL=['SP SERVICES LTD','Your utilities bill','Account holder: MS MICHELLE NG','Blk 123 Example Avenue 4 #05-67','Singapore 560123',
    'Bill date: 05 Oct 2026','Bill period: 01 Sep 2026 - 30 Sep 2026','Please pay by 26 Oct 2026','Page 1 of 1']
NOTICE='Your periodic review needs these documents.\nProof of residential address\nA utility bill or bank statement dated within the last 3 months, showing your full name and address.'


def run(key: str,lines: list[str],pages: int=1,today: date=TODAY):
    template=next(t for t in templates.TEMPLATES if t.key==key)
    return templates.check(template,'\n'.join(lines),pages,NAME,today)


def codes(findings) -> list[tuple[str,str | None]]:
    return [(f.code,f.field) for f in findings]


def test_items_pick_their_template_from_their_own_label() -> None:
    assert templates.template_for(NOTICE).key=='proof_address'
    case='Please send a utility bill, bank statements, a tax form and a source of wealth form.\n'
    assert templates.template_for(case+'Bank statements (last 3 months)\nStatements from your main external bank account.').key=='bank_statements'
    assert templates.template_for(case+'Source of wealth declaration\nComplete and sign the source of wealth form.').key=='wealth_declaration'
    assert templates.template_for(case+'Tax residency self-certification\nComplete the form.').key=='tax_residency'
    assert templates.template_for(case+'Marriage certificate\nA certified copy.') is None


def test_a_recent_singapore_utility_bill_passes() -> None:
    requirements,findings=run('proof_address',BILL)
    assert findings==[] and len(requirements)==4 and all(r.kind=='presence' for r in requirements)


@pytest.mark.parametrize('change,expected',[
    (lambda lines:[l for l in lines if 'MICHELLE' not in l],('missing_field','Your full name, as on your bank records')),
    (lambda lines:[l for l in lines if 'Singapore' not in l],('missing_field','Your Singapore address with its 6-digit postal code')),
    (lambda lines:[l for l in lines if 'SP SERVICES' not in l and 'bill' not in l.lower()],('missing_field','The company that issued it')),
    (lambda lines:[l.replace('Oct 2026','Jun 2026').replace('Sep 2026','May 2026') for l in lines],('date_outdated','A bill or statement date within the last 3 months')),
])
def test_utility_bill_problems_are_named_plainly(change,expected) -> None:
    _,findings=run('proof_address',change(BILL))
    assert expected in codes(findings)


def test_bank_statements_need_each_of_the_last_three_full_months() -> None:
    good=['DBS Bank Ltd','Statement of account','MICHELLE NG','Statement period: 01 Jul 2026 - 30 Sep 2026']
    assert run('bank_statements',good,3)[1]==[]
    _,findings=run('bank_statements',['DBS Bank Ltd','MICHELLE NG','Statement period: 01 Aug 2026 - 31 Aug 2026'],1)
    assert codes(findings)==[('date_outdated','Statements for Jul 2026, Sep 2026')]


def test_declarations_need_a_date_and_signature() -> None:
    good=['Source of wealth declaration','Name: Michelle Ng','My wealth comes from my business since 2015','Date: 20 Oct 2026','Signature: M Ng']
    assert run('wealth_declaration',good)[1]==[]
    assert ('missing_field','Your signature') in codes(run('wealth_declaration',good[:-1])[1])
    tax=['Tax residency self-certification','Name: Michelle Ng','Country of tax residence: Singapore','Date: 20 Oct 2026','Signed by the account holder']
    assert run('tax_residency',tax)[1]==[]


def test_unreadable_files_and_missing_pages_are_explained() -> None:
    assert codes(run('proof_address',[])[1])==[('unassessable','No readable text')]
    assert ('missing_pages','2') in codes(run('proof_address',[*BILL[:-1],'Page 1 of 2'],1)[1])


def test_findings_never_contain_document_values() -> None:
    _,findings=run('proof_address',[l for l in BILL if 'Singapore' not in l])
    assert not any(value in str(findings) for value in ['560123','Example Avenue','MICHELLE'])


@pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Requires native JWT/RLS Data API and storage')
@pytest.mark.parametrize('recent',[False,True])
def test_real_upload_is_checked_by_template_and_only_a_clean_check_can_be_submitted(local_data_api,native_api_client,tmp_path,monkeypatch,recent):
    import hashlib
    import httpx
    import psycopg
    from pydantic import SecretStr
    from app.core.config import settings
    from app.schemas.contracts import ActionCommand
    from tests.storage_support import local_storage,template_pdf
    from tests.test_actions_api import context,headers,owned_case,propose
    from tests.test_document_uploads import upload_target
    ctx=owned_case(local_data_api,context(local_data_api,1002));target=upload_target(local_data_api,1002)
    today=datetime.now(ZoneInfo('Asia/Singapore')).date()
    stamp=today.strftime('%d %b %Y') if recent else date(today.year-1,today.month,1).strftime('%d %b %Y')
    payload=template_pdf([*BILL[:5],'Bill date: '+stamp])
    target.update(file_name='Synthetic bill.pdf',mime_type='application/pdf',size_bytes=len(payload))
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute("update compliance.requested_items set label='Proof of residential address',instructions='A utility bill dated within the last 3 months.' where item_id=%s",(target['item_id'],))
        key=db.execute("select secret from compliance.precheck_keys where key_id='v1'").fetchone()[0]
    monkeypatch.setattr(settings,'precheck_mode','template')
    monkeypatch.setattr(settings,'document_precheck_signing_key',None)
    with local_storage(local_data_api,tmp_path/'blobs') as url:
        monkeypatch.setenv('SUPABASE_URL',url);monkeypatch.setenv('SUPABASE_ANON_KEY','native-public-test-key')
        uploaded=httpx.post(url+f"/storage/v1/object/demobank-compliance-private/1002/{target['case_id']}/{target['document_id']}",
            headers={**headers(ctx),'Content-Type':'application/pdf'},content=payload)
        assert uploaded.status_code==200
        propose(native_api_client,ctx,ActionCommand(type='document.register',idempotency_key=f'{ctx.case_id}:upload:document.register',target=target))
        missing=native_api_client.post(f"/documents/{target['document_id']}/precheck",headers=headers(ctx))
        assert missing.status_code==503 and missing.json()['error']['code']=='precheck_not_configured'
        monkeypatch.setattr(settings,'document_precheck_signing_key',SecretStr(key))
        checked=native_api_client.post(f"/documents/{target['document_id']}/precheck",headers=headers(ctx))
        assert checked.status_code==200,checked.text
        report=checked.json()['data']
        assert report['status']==('no_issues' if recent else 'issues_found'),report['findings']
        assert report['sha256']==hashlib.sha256(payload).hexdigest() and '560123' not in checked.text
        if not recent:assert [f['code'] for f in report['findings']]==['date_outdated']
        submitted=native_api_client.post(f"/documents/{target['document_id']}/submit",headers=headers(ctx))
        assert submitted.status_code==(200 if recent else 422),submitted.text
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)


def test_templates_endpoint_is_public_and_synthetic() -> None:
    described=templates.describe()
    assert [t['key'] for t in described]==['proof_address','bank_statements','wealth_declaration','tax_residency','identity']
    assert all(t['must_show'] and t['example'] for t in described)


def test_templates_route_needs_no_sign_in() -> None:
    from fastapi.testclient import TestClient
    from app.main import app
    response=TestClient(app).get('/documents/templates')
    assert response.status_code==200 and response.json()[0]['key']=='proof_address'


def test_identity_copies_and_translations_need_a_certification() -> None:
    """CR5: a copy that must be certified, or an English translation, is flagged until it carries a certification."""
    passport=['REPUBLIC OF EXAMPLE','Passport','Name: MICHELLE NG','Date of expiry: 14 Mar 2031']
    _,plain=run('identity',passport)
    assert ('missing_field',templates.CERTIFICATION) in codes(plain)
    _,certified=run('identity',[*passport,'Certified true copy of the original, signed by A. Lim, Notary Public, 20 Oct 2026'])
    assert ('missing_field',templates.CERTIFICATION) not in codes(certified)
    _,translated=run('proof_address',[*BILL,'English translation of the original bill'])
    assert ('missing_field',templates.TRANSLATOR) in codes(translated)
    _,sworn=run('proof_address',[*BILL,'English translation of the original bill. I hereby certify that this translation is true and complete.'])
    assert ('missing_field',templates.TRANSLATOR) not in codes(sworn)
    _,form=run('tax_residency',['Tax residency self-certification','Name: MICHELLE NG','Country of tax residence: Singapore',
        'Date: 20 Oct 2026','Signature: M Ng'])
    assert not any(field in (templates.CERTIFICATION,templates.TRANSLATOR) for _,field in codes(form))
