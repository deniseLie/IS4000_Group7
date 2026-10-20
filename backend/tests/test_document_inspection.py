"""Real SQL/JWT/RLS source verification. Blob decoding is exercised separately against actual hosted Storage."""
import asyncio
import os
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from app.services import customer_data
from tests.data_api_support import NativeAPI
from tests.test_actions_api import context,headers,owned_case,propose
from tests.test_document_uploads import object_metadata,upload_target
from app.schemas.contracts import ActionCommand

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Opt in to native PostgreSQL/PostgREST')


@pytest.mark.parametrize('cid',[1001,1002,1003])
def test_inspection_source_is_owned_registered_and_matches_platform_metadata(local_data_api: NativeAPI,native_api_client: TestClient,cid: int) -> None:
    import psycopg
    from uuid import UUID
    ctx=owned_case(local_data_api,context(local_data_api,cid));target=upload_target(local_data_api,cid)
    object_id=object_metadata(local_data_api,cid,target)
    command=ActionCommand(type='document.register',idempotency_key=f'{ctx.case_id}:inspect-source:document.register',target=target)
    propose(native_api_client,ctx,command)
    snapshot=asyncio.run(customer_data.get_document_object(UUID(target['document_id']),ctx))
    assert snapshot.data.document_id==UUID(target['document_id']) and snapshot.data.object_id==object_id
    assert snapshot.data.size_bytes==123 and snapshot.data.mime_type=='image/png'
    for foreign in [value for value in [1001,1002,1003] if value!=cid]:
        assert native_api_client.get(f"/documents/{target['document_id']}/inspection",headers=headers(context(local_data_api,foreign))).status_code==404
    assert native_api_client.get(f"/documents/{target['document_id']}/inspection").status_code==401
    with psycopg.connect(local_data_api.database_uri) as db:
        db.execute("update storage.objects set metadata=jsonb_set(metadata,'{size}','124') where id=%s",(object_id,))
    assert asyncio.run(customer_data.get_document_object(UUID(target['document_id']),ctx)).data is None
    assert native_api_client.get(f"/documents/{target['document_id']}/inspection",headers=headers(ctx)).status_code==404
    with psycopg.connect(local_data_api.database_uri) as db:
        assert db.execute('select first_bad_event_id from audit.verify_chain(null)').fetchone()==(None,)
