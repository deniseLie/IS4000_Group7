import test from 'node:test';
import assert from 'node:assert/strict';
import {parseWorkflow,parsePausedWorkflow,parsePreview} from './workflows.js';

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
test('paused preview shows every section and offers exactly the pending block on the urgent one',()=>{
  const workflow=waiting(),action={command_id:workflow.pending_commands[0],type:'card.temporary_block',summary:'Review a temporary block for synthetic card ending 4821',expires_at:'2026-10-19T12:05:00Z'};
  const section=(task_id,domain,priority,pending_action=null)=>({task_id,domain,priority,answer:`Answer ${task_id}`,sources:[],response_origin:'ai',pending_action});
  const preview={answer:'x',disclaimer:'',source_check:'basic_checks_passed',response_origin:'ai',
    sections:[section('t1','fraud','P0',action),section('t2','retail','P3'),section('t3','retail','P3')]};
  assert.deepEqual(parsePreview(preview,workflow),preview);
  assert.deepEqual(parsePausedWorkflow({workflow,actions:[action],preview},workflow.session_id),workflow);
  assert.throws(()=>parsePreview({...preview,sections:[]},workflow));
  assert.throws(()=>parsePreview({...preview,sections:[section('t1','fraud','P0'),preview.sections[1]]},workflow));
  assert.throws(()=>parsePreview({...preview,sections:[section('t1','fraud','P0',{...action,command_id:workflow.turn_id}),preview.sections[1]]},workflow));
  assert.throws(()=>parsePreview({...preview,sections:[preview.sections[0],section('t2','retail','P3',action)]},workflow));
  assert.throws(()=>parsePreview({...preview,sections:[section('t1','retail','P3',action)]},workflow));
});
