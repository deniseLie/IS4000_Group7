import json
import re
from pathlib import Path
import pytest
from app.services.privacy import redact
from app.services.answers import check_answer

CASES=json.loads((Path(__file__).resolve().parents[2]/'tests/fixtures/privacy-cases.json').read_text())

@pytest.mark.parametrize('case',CASES)
def test_shared_privacy_cases(case):
    cleaned,changed=redact(case['text'])
    if case['secret']:
        assert changed and case['secret'] not in cleaned
        assert all(part not in cleaned for part in re.split(r'[\s-]+',case['secret']) if len(part)>=3)
        assert '[private detail removed]' in cleaned
        raw=json.dumps({'answer':case['text'],'source_ids':['security'],'answer_kind':'banking'})
        assert check_answer(raw,[{'id':'security','text':case['text']}])[2]
    else:assert (cleaned,changed)==(case['text'],False)
