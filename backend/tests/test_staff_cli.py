"""Run the actual CLI subprocess against real JWT/PostgREST/SQL through a path-only routing adapter."""
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from pydantic import JsonValue,TypeAdapter
from app.schemas.actions import ActionOutcome
from tests.data_api_support import NativeAPI
from tests.staff_cli_support import cli_gateway
from tests.test_staff_support import block,handoff,staff_token,staff_user

pytestmark=pytest.mark.skipif(os.getenv('TEST_DATA_API_LOCAL')!='1',reason='Opt in to native staff CLI integration')


@pytest.mark.usefixtures('legacy_card_actions')  # vehicle: retired client card actions (tests/legacy_card_actions.py)
def test_real_staff_cli_queue_claim_unblock_cancel_close_and_retry(local_data_api: NativeAPI,native_api_client: TestClient,tmp_path: Path) -> None:
    ctx,hid=handoff(local_data_api,native_api_client,'P0')
    token=staff_token(local_data_api,staff_user(local_data_api),'aal2',{'demobank_role':'support'})
    with cli_gateway(local_data_api) as url:
        env={'SUPABASE_URL':url,'SUPABASE_ANON_KEY':'sb_publishable_native_cli_test_public_key','STAFF_ACCESS_TOKEN':token}
        env_file=tmp_path/'.env.staff'
        with os.fdopen(os.open(env_file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as output:
            output.write('\n'.join(key+'='+value for key,value in env.items())+'\n')
        environment={key:value for key,value in os.environ.items() if key in {'PATH','LANG','LC_ALL','TMPDIR'}}
        # The explicitly selected private file must take precedence over unrelated shell configuration.
        environment.update({'SUPABASE_URL':'http://unrelated.example.invalid','SUPABASE_ANON_KEY':'unrelated-shell-value',
            'STAFF_ACCESS_TOKEN':'unrelated-shell-value'})
        def command(args: list[str]) -> JsonValue:
            result=subprocess.run([sys.executable,'-m','scripts.staff_support','--env-file',str(env_file),*args],
                cwd=Path(__file__).parents[1],env=environment,capture_output=True,text=True,timeout=20)
            assert token not in result.stdout and token not in result.stderr
            assert result.returncode==0,result.stderr
            return TypeAdapter(JsonValue).validate_json(result.stdout)
        assert len(command(['queue','--limit','20']))==1
        claim_id=str(uuid4())
        claimed=command(['claim','--handoff',str(hid),'--command',claim_id])
        assert command(['claim','--handoff',str(hid),'--command',claim_id])==claimed
        block(native_api_client,ctx,'cli-unblock')
        unblocked=ActionOutcome.model_validate(command(['unblock','--handoff',str(hid),'--command',str(uuid4()),'--card','card_10011']))
        assert unblocked.receipt.result.status=='active'
        block(native_api_client,ctx,'cli-cancel')
        cancelled=ActionOutcome.model_validate(command(['cancel','--handoff',str(hid),'--command',str(uuid4()),'--card','card_10011']))
        assert cancelled.receipt.result.status=='cancelled'
        closed=ActionOutcome.model_validate(command(['close','--handoff',str(hid),'--command',str(uuid4())]))
        assert closed.receipt.result.status=='closed' and command(['queue','--limit','20'])==[]
