import test from 'node:test';
import assert from 'node:assert/strict';
import {parseWorkflow,parsePausedWorkflow} from './workflows.js';

function waiting() {
  return {version:'v2-actions-v1',turn_id:'12345678-1234-1234-1234-123456789011',session_id:'12345678-1234-1234-1234-123456789012',
    case_id:'12345678-1234-1234-1234-123456789013',customer_id:1001,state:'awaiting_confirmation',ready:true,can_resume:false,
    pending_commands:['12345678-1234-1234-1234-123456789014'],lease_expires_at:null,error_code:null};
}
test('workflow metadata cannot claim resumability with pending or unready decisions',()=>{
  const value=waiting(),before=JSON.stringify(value);
  assert.deepEqual(parseWorkflow(value),value);
  for(const change of [{can_resume:true},{pending_commands:[...value.pending_commands,...value.pending_commands]},
    {ready:false,can_resume:true,pending_commands:[]},{state:'completed',can_resume:true,pending_commands:[]},{customer_id:'1001'}])
    assert.throws(()=>parseWorkflow({...value,...change}));
  assert.equal(JSON.stringify(value),before);
});
test('review SSE belongs to the current conversation and exact pending command set',()=>{
  const workflow=waiting();
  const value={workflow,actions:[{command_id:workflow.pending_commands[0],type:'card.temporary_block',summary:'Review synthetic card',expires_at:'2026-10-19T12:05:00Z'}]};
  assert.deepEqual(parsePausedWorkflow(value,workflow.session_id),workflow);
  assert.throws(()=>parsePausedWorkflow(value,workflow.case_id));
  assert.throws(()=>parsePausedWorkflow({...value,actions:[]},workflow.session_id));
  assert.throws(()=>parsePausedWorkflow({...value,actions:[{...value.actions[0],command_id:workflow.turn_id}]},workflow.session_id));
});
