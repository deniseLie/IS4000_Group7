import test from 'node:test';
import assert from 'node:assert/strict';
import {parseAction,parseActions} from './actions.js';

function proposal() {
  const command_id='12345678-1234-1234-1234-123456789012',case_id='12345678-1234-1234-1234-123456789013';
  return {command:{command_id,case_id,customer_id:1001,type:'card.temporary_block',target:{card_id:'card_10011',last4:'4821'},
    idempotency_key:`${case_id}:block:card.temporary_block`,state:'proposed',proposed_at:'2026-10-19T12:00:00Z',
    expires_at:'2026-10-19T12:05:00Z',confirmed_at:null},receipt:null};
}
function completed() {
  const value=proposal();
  return {command:{...value.command,state:'succeeded',confirmed_at:'2026-10-19T12:01:00Z'},
    receipt:{receipt_id:`rcpt_${value.command.command_id.replaceAll('-','')}`,command_id:value.command.command_id,customer_id:1001,
      outcome:'succeeded',result:{card_id:'card_10011',status:'temporarily_blocked'},audit_event_id:51,created_at:'2026-10-19T12:01:00Z'}};
}

test('action parsing preserves verified pending and receipt states without mutating input',()=>{
  const value=completed(),before=JSON.stringify(value);
  const result=parseAction({...value,unrelated:'ignored'});
  assert.equal(result.receipt.outcome,'succeeded');
  assert.equal(result.command.target.last4,'4821');
  assert.equal(JSON.stringify(value),before);
  assert.equal(parseAction(proposal()).receipt,null);
  assert.deepEqual(parseActions([]),[]);
});

test('an action cannot acquire success from an inconsistent or unrelated receipt',()=>{
  const value=completed();
  for(const changed of [
    {...value,receipt:null},
    {...value,receipt:{...value.receipt,command_id:'12345678-1234-1234-1234-123456789099'}},
    {...value,receipt:{...value.receipt,customer_id:1003}},
    {...value,receipt:{...value.receipt,audit_event_id:null}},
    {...value,receipt:{...value.receipt,result:{card_id:'card_10031',status:'temporarily_blocked'}}},
    {...value,command:{...value.command,confirmed_at:null}},
    {...value,command:{...value.command,state:'rejected'}},
  ])assert.throws(()=>parseAction(changed));
  assert.throws(()=>parseActions([value,value]));
});

test('failed, expired and rejected commands retain their actual outcome',()=>{
  const value=completed();
  const failed={command:{...value.command,state:'failed'},receipt:{...value.receipt,outcome:'failed',result:{error_code:'target_changed'}}};
  assert.equal(parseAction(failed).receipt.outcome,'failed');
  assert.throws(()=>parseAction({...failed,receipt:{...failed.receipt,result:{card_id:'card_10011',status:'temporarily_blocked'}}}));
  for(const state of ['expired','rejected'])assert.equal(parseAction({command:{...proposal().command,state},receipt:null}).command.state,state);
});
