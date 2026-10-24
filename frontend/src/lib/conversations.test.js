import test from 'node:test';
import assert from 'node:assert/strict';
import {parseHistory,parseSavedAnswer,parseMetadata} from './conversations.js';

const id='00000000-0000-4000-8000-000000000001';
function session() {return {session_id:id,mode:'public',language:'English',revision:1,active_turn_id:null,
  started_at:'2026-10-19T12:00:00Z',last_active_at:'2026-10-19T12:00:00Z',expires_at:null};}
function answer() {return {answer:'Contact official support.',disclaimer:'Synthetic demo.',response_origin:'safety',source_check:'fixed_guidance',needs_human:true,prompt_version:null,sources:[]};}
function history() {return {session:session(),history_trimmed:false,active_turn:null,messages:[
  {message_id:id,role:'user',redacted_text:'Help please',payload:null,created_at:'2026-10-19T12:00:00Z',sequence:1},
  {message_id:id,role:'assistant',redacted_text:answer().answer,payload:answer(),created_at:'2026-10-19T12:00:01Z',sequence:2}]};}

test('saved history preserves real reply origin and treats stored failures as errors',()=>{
  const value=history(),before=structuredClone(value);
  assert.equal(parseHistory(value).messages[1].response_origin,'safety');
  assert.deepEqual(value,before);
  const message={...value.messages[1],redacted_text:'Reply cancelled',payload:{error:{code:'cancelled',message:'Reply cancelled'}}};
  const result=parseHistory({...value,messages:[value.messages[0],message]}).messages[1];
  assert.equal(result.error,true);assert.equal(result.response_origin,undefined);
});
test('saved history rejects inconsistent activity, order and payloads',()=>{
  assert.throws(()=>parseHistory({...history(),active_turn:{turn_id:id,session_id:id,state:'processing'}}));
  assert.throws(()=>parseHistory({...history(),messages:history().messages.toReversed()}));
  assert.throws(()=>parseHistory({...history(),messages:[{...history().messages[1],redacted_text:'changed'}]}));
  assert.throws(()=>parseMetadata({conversation_id:id,revision:-1,active_turn_id:null}));
});
test('saved sources must use the local source route and approved publisher links',()=>{
  for(const url of ['https://example.invalid','/knowledge/../../secret'])assert.throws(()=>parseSavedAnswer({...answer(),sources:[{id:'a',title:'A',version:'v1',url}]}));
  assert.throws(()=>parseSavedAnswer({...answer(),sources:[{id:'a',title:'A',version:'v1',url:'/knowledge/a',reference_url:'https://example.invalid'}]}));
});
test('saved replies keep the recorded card-block decision on its section',()=>{
  const section={task_id:'t1',domain:'fraud',priority:'P0',answer:'Withheld.',response_origin:'validation',prompt_version:null,sources:[],
    decision:'Your card ending 4821 is temporarily blocked. This did not reverse any transaction.'};
  const saved={answer:'x',disclaimer:'',response_origin:'validation',source_check:'rejected',needs_human:true,prompt_version:null,sources:[],sections:[section,{...section,task_id:'t2',domain:'retail',priority:'P3',decision:undefined}]};
  const parsed=parseSavedAnswer(saved);
  assert.equal(parsed.sections[0].decision,section.decision);
  assert.equal(parsed.sections[1].decision,null);
  assert.throws(()=>parseSavedAnswer({...saved,sections:[{...section,decision:42}]}));
});
