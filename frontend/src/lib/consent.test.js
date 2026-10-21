import test from 'node:test';
import assert from 'node:assert/strict';
import {parseSharingStatus} from './consent.js';

function status() {
  return {purpose:'rm_brief',categories:['profile'],recipient:{rm_id:'00000000-0000-4000-8000-000000000001',name:'Synthetic manager'},
    consent:{granted:true,grant_id:'00000000-0000-4000-8000-000000000002',expires_at:'2026-10-20T00:00:00Z',checked_at:'2026-10-19T00:00:00Z'}};
}

test('sharing parser preserves verified status without unrelated fields or input mutation',()=>{
  const source={...status(),unrelated:'ignored'};
  const before=structuredClone(source);
  assert.equal(parseSharingStatus(source).consent.granted,true);
  assert.equal(parseSharingStatus(source).unrelated,undefined);
  assert.deepEqual(source,before);
});

test('no assigned manager is a distinct state from denied consent',()=>{
  assert.equal(parseSharingStatus({...status(),recipient:null,consent:null}).recipient,null);
  assert.equal(parseSharingStatus({...status(),consent:{...status().consent,granted:false,grant_id:null,expires_at:null}}).consent.granted,false);
  assert.throws(()=>parseSharingStatus({...status(),recipient:null}));
});

test('invalid consent cannot become an allowed status',()=>{
  for(const change of [{granted:1},{grant_id:null},{expires_at:'2026-10-18T00:00:00Z'},
    {checked_at:'not a date'},{expires_at:'2026-10-20T00:00:00'}]) {
    assert.throws(()=>parseSharingStatus({...status(),consent:{...status().consent,...change}}));
  }
  assert.throws(()=>parseSharingStatus({...status(),categories:['accounts']}));
  assert.throws(()=>parseSharingStatus({...status(),recipient:{...status().recipient,name:' '}}));
});
