import test from 'node:test';
import assert from 'node:assert/strict';
import {parseRequests,parseSlots} from './relationship.js';

function envelope(data) {return {call_id:'00000000-0000-4000-8000-000000000001',as_of:'2026-10-19T12:00:00Z',data};}
test('relationship envelopes retain valid empty results and reject unverified records',()=>{
  assert.deepEqual(parseRequests(envelope([])).data,[]);
  assert.deepEqual(parseSlots(envelope([])).data,[]);
  for(const value of [{...envelope([]),call_id:null},{...envelope([]),as_of:'invalid'},{...envelope([]),data:null}])assert.throws(()=>parseRequests(value));
  assert.throws(()=>parseRequests(envelope([{request_id:'bad',who:'Synthetic',what:'Test',status:'sent',urgency:'normal',deadline:null}])));
});
test('appointment parsing rejects incorrect durations without mutating the API record',()=>{
  const row={advisor_id:'00000000-0000-4000-8000-000000000002',starts_at:'2026-10-20T09:00:00+08:00',ends_at:'2026-10-20T09:30:00+08:00',ignored:'extra'};
  const before=structuredClone(row);
  assert.equal(parseSlots(envelope([row])).data[0].ignored,undefined);
  assert.deepEqual(row,before);
  assert.throws(()=>parseSlots(envelope([{...row,ends_at:row.starts_at}])));
});
