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

function dispute(state='succeeded',answers={card_in_possession:'yes'}) {
  const command_id='12345678-1234-1234-1234-123456789022',case_id='12345678-1234-1234-1234-123456789013';
  const items=[{transaction_id:'txn_100115',date:'2026-10-12',merchant:'ONLINE MERCHANT 8831',amount:-1,currency:'SGD'},
    {transaction_id:'txn_100111',date:'2026-10-17',merchant:'ONLINE MERCHANT 8831',amount:-2400,currency:'SGD'}];
  const target={card_id:'card_10011',transaction_ids:['txn_100111','txn_100115'],last4:'4821',items};
  return {command:{command_id,case_id,customer_id:1001,type:'card.dispute',target,idempotency_key:`${case_id}:t1_dispute:card.dispute`,state,
    proposed_at:'2026-10-19T12:00:00Z',expires_at:'2026-10-19T12:05:00Z',confirmed_at:state==='proposed'?null:'2026-10-19T12:01:00Z',confirmation_input:state==='proposed'?null:answers},
    receipt:state!=='succeeded'?null:{receipt_id:`rcpt_${command_id.replaceAll('-','')}`,command_id,customer_id:1001,outcome:'succeeded',
      result:{...target,card_in_possession:answers.card_in_possession,status:'submitted_for_review'},audit_event_id:52,created_at:'2026-10-19T12:01:00Z'}};
}

test('a dispute draft keeps its exact charges and the customer answer about the card',()=>{
  const recorded=parseAction(dispute());
  assert.deepEqual(recorded.command.answers,{card_in_possession:'yes'});
  assert.deepEqual(recorded.command.target.items.map(item=>item.amount),[-1,-2400]);
  assert.equal(parseAction(dispute('proposed')).command.answers,null);
  const value=dispute();
  for(const changed of [
    {...value,receipt:{...value.receipt,result:{...value.receipt.result,card_in_possession:'no'}}},
    {...value,receipt:{...value.receipt,result:{...value.receipt.result,items:value.receipt.result.items.slice(0,1),transaction_ids:['txn_100115']}}},
    {...value,command:{...value.command,confirmation_input:null}},
    {...value,command:{...value.command,confirmation_input:{card_in_possession:'maybe'}}},
    {...value,command:{...value.command,target:{...value.command.target,items:[{...value.command.target.items[0],amount:1},value.command.target.items[1]]}}},
    {...proposal(),command:{...proposal().command,confirmation_input:{card_in_possession:'yes'}}},
  ])assert.throws(()=>parseAction(changed));
});

test('a customer card change is told apart from the staff operation of the same type',()=>{
  const command_id='12345678-1234-1234-1234-123456789031',case_id='12345678-1234-1234-1234-123456789013';
  const base={command_id,case_id,customer_id:1001,type:'card.unblock',target:{card_id:'card_10011',last4:'4821',from_status:'temporarily_blocked'},
    idempotency_key:`${case_id}:manage_card:card.unblock`,state:'proposed',proposed_at:'2026-10-19T12:00:00Z',expires_at:'2026-10-19T12:05:00Z',confirmed_at:null};
  const proposed=parseAction({command:base,receipt:null});
  assert.equal(proposed.command.staff,false);
  assert.equal(proposed.command.target.from_status,'temporarily_blocked');
  const done={command:{...base,state:'succeeded',confirmed_at:'2026-10-19T12:01:00Z'},receipt:{receipt_id:`rcpt_${command_id.replaceAll('-','')}`,command_id,customer_id:1001,
    outcome:'succeeded',result:{card_id:'card_10011',last4:'4821',status:'active',from_status:'temporarily_blocked',handoffs_noted:['12345678-1234-1234-1234-123456789099']},audit_event_id:61,created_at:'2026-10-19T12:01:00Z'}};
  assert.deepEqual(parseAction(done).receipt.result.handoffs_noted,['12345678-1234-1234-1234-123456789099']);
  for(const changed of [
    {...done,command:{...done.command,confirmed_at:null}},
    {...done,receipt:{...done.receipt,result:{...done.receipt.result,from_status:'active'}}},
    {...done,receipt:{...done.receipt,result:{...done.receipt.result,handoffs_noted:['not-a-uuid']}}},
    {command:{...base,target:{...base.target,from_status:'active'}},receipt:null},
    {command:{...base,target:{...base.target,handoff_id:'12345678-1234-1234-1234-123456789099'}},receipt:null},
  ])assert.throws(()=>parseAction(changed));
});
