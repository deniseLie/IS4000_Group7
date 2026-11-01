import test from 'node:test';
import assert from 'node:assert/strict';
import {contextAfter,dayCells,followupGroups,historyFor,interactionPayload,isRm,missingFields,replyText,requestContext,validationNote} from './rm.js';

test('only an administrator-set rm role opens the RM workspace',()=>{
  assert.equal(isRm({user:{app_metadata:{demobank_role:'rm'}}}),true);
  assert.equal(isRm({user:{app_metadata:{demobank_role:'support'}}}),false);
  assert.equal(isRm({user:{user_metadata:{demobank_role:'rm'}}}),false);
  assert.equal(isRm(null),false);
});

test('history keeps the client of each turn, redacts and starts with the RM',()=>{
  const messages=[{role:'assistant',reply:{summary:'hello'}},{role:'user',content:'card 4111 1111 1111 1111 for David',client_id:1003},
    {role:'assistant',reply:{summary:'Done',sections:[{heading:'H',items:[{text:'a point'}]}]},client_id:1003},{role:'assistant',pending:true}];
  const turns=historyFor(messages);
  assert.equal(turns[0].role,'user');
  assert.match(turns[0].content,/private detail removed/);
  assert.equal(turns[1].client_id,1003);
  assert.match(turns[1].content,/- a point/);
  assert.equal(turns.length,2);
});

test('switching client drops the other client’s draft and documentation',()=>{
  const previous={client_id:1003,ticket_ref:'TCK-001001',appointment_id:'a',note_id:null,draft:{customer_id:1003,text:'x',revision:1},
    documentation:{customer_id:1003,source_notes:'n'}};
  const same=contextAfter(previous,{context:{client_id:1003}});
  assert.equal(same.draft.text,'x');assert.equal(same.ticket_ref,'TCK-001001');
  const other=contextAfter(previous,{context:{client_id:1004}});
  assert.equal(other.draft,null);assert.equal(other.documentation,null);assert.equal(other.ticket_ref,null);assert.equal(other.appointment_id,null);
  const body=requestContext({...previous,documentation:{...previous.documentation,client:'David',from_note:true,corrections:['c']}});
  assert.deepEqual(Object.keys(body.documentation).sort(),['corrections','customer_id','draft','note_id','revision','source_notes']);
});

test('withheld points are counted, never shown',()=>{
  assert.equal(validationNote({withheld:0}),'');
  assert.equal(validationNote({withheld:1}),'1 point could not be verified against your records and was left out.');
  assert.match(validationNote({withheld:2}),/2 points .* were left out/);
  assert.match(replyText({summary:'S',draft:{text:'D'}}),/Draft: D/);
});

test('follow-ups group by date and owner',()=>{
  const g=followupGroups([{kind:'task',owner:'rm',due_on:'2026-10-30'},{kind:'milestone',owner:'rm',due_on:'2026-11-05'},
    {kind:'milestone',owner:'rm',due_on:'2026-12-20'},{kind:'specialist_ticket',owner:'specialist',due_on:'2026-11-02'},{kind:'commitment',owner:'rm',due_on:null}],'2026-11-01');
  assert.deepEqual([g.overdue.length,g.week.length,g.later.length,g.specialist.length,g.commitments.length],[1,1,1,1,1]);
});

test('availability cells show booked, held, blocked and free half hours',()=>{
  const cells=dayCells({hours:{starts:'09:00',ends:'11:00'},free:['10:30'],blocks:[{block_id:'b1',starts:'09:30',ends:'10:00'}],
    bookings:[{client:'David Lee',status:'confirmed',starts:'09:00',ends:'09:30'},{client:'Grace',status:'held',starts:'10:00',ends:'10:30'}]});
  assert.deepEqual(cells.map(c=>c.state),['booked','blocked','held','free']);
  assert.equal(cells[1].block_id,'b1');
  assert.deepEqual(dayCells({hours:null,free:[],blocks:[],bookings:[]}),[]);
});

test('approved documentation keeps the RM notes, corrections and certainty',()=>{
  const doc={customer_id:1003,source_notes:'notes',corrections:['the statement is for August'],draft:{gaps:['g'],actions:[{kind:'follow_up_task',title:'t',origin:'notes'}]}};
  const items=[{category:'commitment',text:'Send the USD amount',certainty:'confirmed'},{category:'client_request',text:'Update mobile',certainty:'tentative'},
    {category:'decision',text:'removed one',certainty:'confirmed',removed:true}];
  const body=interactionPayload(doc,{met_on:'2026-11-01',format:'in_person',title:'Intro',summary:'S',items},'k');
  assert.deepEqual(body.commitments,['Send the USD amount']);
  assert.deepEqual(body.client_requests,['Update mobile (tentative)']);
  assert.deepEqual(body.decisions,[]);
  assert.equal(body.source_notes,'notes');
  assert.deepEqual(body.revisions,[{revision:2,correction:'the statement is for August',confirmed_by:'rm'}]);
  assert.deepEqual(missingFields({fields:[{key:'account',label:'Account or card',required:true},{key:'x',label:'X',required:false}]},{},'s'),['Account or card']);
});

test('saved conversations drop source records, keep clients and fit the request size',async()=>{
  const {conversationPayload,restoreConversation,SAVE_LIMIT}=await import('./rm.js');
  const reply={summary:'S',sections:[],sources:[{id:'profile',title:'Client profile',record:{name:'Michelle'}}]};
  const messages=[{role:'user',content:'Overview of Michelle',client_id:1002},{role:'assistant',reply,client_id:1002},{role:'assistant',pending:true}];
  const saved=conversationPayload(messages,{client_id:1002,draft:null,documentation:null});
  assert.equal(saved.title,'Overview of Michelle');
  assert.equal(saved.messages.length,2);
  assert.equal(saved.messages[1].reply.sources[0].record,undefined);
  assert.equal(saved.context.client_id,1002);
  const long=Array.from({length:60},(_,i)=>[{role:'user',content:'x'.repeat(2000)+i,client_id:1002},{role:'assistant',reply,client_id:1002}]).flat();
  const trimmed=conversationPayload(long,{client_id:1002});
  assert.ok(JSON.stringify(trimmed).length<=SAVE_LIMIT+500 && trimmed.messages[0].role==='user' && trimmed.messages.length<long.length);
  const restored=restoreConversation({messages:saved.messages,context:{client_id:1002}});
  assert.ok(restored.messages.every(m=>m.restored)) ;assert.equal(restored.context.draft,null);
});
