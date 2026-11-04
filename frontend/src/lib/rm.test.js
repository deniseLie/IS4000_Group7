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
  assert.deepEqual(Object.keys(body.documentation).sort(),['appointment_id','corrections','customer_id','draft','note_id','revision','source_notes']);
});

test('withheld points are counted, never shown',()=>{
  assert.equal(validationNote({withheld:0}),'');
  assert.equal(validationNote({withheld:1}),'1 point could not be verified against your records and was left out.');
  assert.match(validationNote({withheld:2}),/2 points .* were left out/);
  assert.match(replyText({summary:'S',draft:{text:'D'}}),/Draft: D/);
});

test('a reply with one draft per client lists each (R21)',async ()=>{
  const {allDrafts,reportText}=await import('./rm.js');
  const reply={intent:'reviews',summary:'S',sections:[],drafts:[{client:'Michelle',text:'Hi Michelle'},{client:'David',text:'Hi David'}]};
  assert.equal(allDrafts(reply).length,2);
  assert.deepEqual(allDrafts({draft:{text:'D'}}),[{text:'D'}]);
  assert.deepEqual(allDrafts({}),[]);
  assert.match(replyText(reply),/Draft to Michelle: Hi Michelle\nDraft to David: Hi David/);
  assert.match(reportText(reply),/Draft to David \(not sent\):\nHi David/);
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

test('ticket age reads as plain words and the book filters as you type',async()=>{
  const {receivedText,filterBook}=await import('./rm.js');
  assert.equal(receivedText(0),'Received less than an hour ago');
  assert.equal(receivedText(1),'Received 1 hour ago');
  assert.equal(receivedText(26),'Received 26 hours ago');
  assert.equal(receivedText(100),'Received 4 days ago');
  const book=[{name:'Grace Koh'},{name:'Michelle Ng'},{name:'Tan Mei Ling'}];
  assert.deepEqual(filterBook(book,'gr').map(c=>c.name),['Grace Koh']);
  assert.deepEqual(filterBook(book,'mei').map(c=>c.name),['Tan Mei Ling']);
  assert.equal(filterBook(book,'  ').length,3);
  assert.equal(filterBook(book,'wei ming').length,0);
});

test('meetings are grouped by day and counted per client per week',async()=>{
  const {meetingsPerWeek,weekStart,groupByDate}=await import('./rm.js');
  assert.equal(weekStart('2026-11-01'),'2026-10-26');  // Sunday belongs to the week starting Monday
  assert.equal(weekStart('2026-11-02'),'2026-11-02');
  const list=[{customer_id:1002,date:'2026-11-02'},{customer_id:1002,date:'2026-11-02'},{customer_id:1002,date:'2026-11-06'},{customer_id:1004,date:'2026-11-09'}];
  const counts=meetingsPerWeek(list);
  assert.equal(counts.get('1002|2026-11-02'),3);assert.equal(counts.get('1004|2026-11-09'),1);
  assert.deepEqual(groupByDate(list).map(g=>[g.date,g.items.length]),[['2026-11-02',2],['2026-11-06',1],['2026-11-09',1]]);
});

test('a copied report keeps labels, numbered references and the source list',async()=>{
  const {reportText}=await import('./rm.js');
  const text=reportText({intent:'meeting_brief',summary:'S',sections:[{heading:'Open matters',items:[{text:'FD matures 11 Nov',kind:'record',source_ids:['milestones','x']},
    {text:'Ask about the sale agreement',kind:'suggestion',source_ids:['note-2026-09-24']}]}],sources:[{id:'milestones',title:'Upcoming maturities'},{id:'note-2026-09-24',title:'Meeting note 24 Sep 2026'}]},'Michelle Ng');
  assert.match(text,/^Meeting briefing – Michelle Ng/);
  assert.match(text,/- FD matures 11 Nov \[1\]/);
  assert.match(text,/- AI suggestion: Ask about the sale agreement \[2\]/);
  assert.match(text,/\[2\] Meeting note 24 Sep 2026/);
});

test('Today page lists upcoming meetings, meetings to document, waiting requests and what is coming up',async()=>{
  const {todayView,interactionPayload}=await import('./rm.js');
  const v=todayView({appointments:[
      {appointment_id:'a1',status:'confirmed',past:true,date:'2026-10-30',note_id:null},
      {appointment_id:'a2',status:'confirmed',past:true,date:'2026-10-28',note_id:'n1'},
      {appointment_id:'a3',status:'confirmed',past:false,date:'2026-11-02'},
      {appointment_id:'a4',status:'held',past:false,date:'2026-11-03'},
      {appointment_id:'a5',status:'confirmed',past:false,date:'2026-11-20'}],
    tickets:{rm_owned:[{reference:'T1',overdue:false,age_hours:3},{reference:'T2',overdue:true,age_hours:40}]},
    followups:[{item_key:'m',kind:'milestone',owner:'rm',due_on:'2026-11-12'},{item_key:'x',kind:'milestone',owner:'rm',due_on:'2027-01-12'},
      {item_key:'t',kind:'task',owner:'rm',overdue:true,due_on:'2026-10-30'},{item_key:'s',kind:'specialist_ticket',owner:'specialist',due_on:'2026-11-03'}]},'2026-11-02');
  assert.deepEqual(v.upcoming.map(a=>a.appointment_id),['a3']);
  assert.deepEqual(v.today.map(a=>a.appointment_id),['a3']);
  assert.deepEqual(v.toDocument.map(a=>a.appointment_id),['a1']);
  assert.deepEqual(v.requests.map(t=>t.reference),['T2','T1']);
  assert.equal(v.pastTarget.length,1);assert.equal(v.fresh.length,1);
  assert.deepEqual(v.soon.map(f=>f.item_key),['m']);assert.deepEqual(v.overdueTasks.map(f=>f.item_key),['t']);
  const body=interactionPayload({customer_id:1002,source_notes:'n',appointment_id:'a1',draft:{gaps:[],actions:[]}},{met_on:'2026-10-30',format:'phone',summary:'S',items:[]},'k');
  assert.equal(body.structured.appointment_id,'a1');
});

test('meeting requests to answer: overdue first, new ones marked, waiting on the client left out (2a)',async()=>{
  const {requestsToAnswer,needsAnswer,requestLabel,topicLabel,slotProblemText}=await import('./rm.js');
  const now=Date.parse('2026-11-05T10:00:00Z');
  const list=[{request_id:'fresh',status:'requested',overdue:false,created_at:'2026-11-05T08:00:00Z',respond_by:'2026-11-06T08:00:00Z'},
    {request_id:'late',status:'requested',overdue:true,created_at:'2026-11-03T08:00:00Z',respond_by:'2026-11-04T08:00:00Z'},
    {request_id:'held',status:'countered',proposal_lapsed:false,created_at:'2026-11-01T08:00:00Z',respond_by:'2026-11-02T08:00:00Z'},
    {request_id:'lapsed',status:'countered',proposal_lapsed:true,created_at:'2026-10-28T08:00:00Z',respond_by:'2026-10-29T08:00:00Z'},
    {request_id:'done',status:'scheduled',created_at:'2026-10-20T08:00:00Z',respond_by:'2026-10-21T08:00:00Z'}];
  const due=requestsToAnswer(list,now);
  assert.deepEqual(due.map(r=>r.request_id),['late','lapsed','fresh']);
  assert.deepEqual(due.map(r=>r.isNew),[false,false,true]);
  assert.deepEqual(list.map(needsAnswer),[true,true,false,true,false]);
  assert.deepEqual(list.map(requestLabel),['Waiting for you','Overdue','Waiting for the client','Proposal lapsed','Scheduled']);
  assert.equal(topicLabel('term_sheet'),'Term sheet');assert.equal(topicLabel('new_code'),'new code');
  assert.equal(slotProblemText('weekly_limit'),'the client already has the most meetings allowed that week');assert.equal(slotProblemText(null),'');
});

test('a decision body fits its outcome and the card id survives a retry until it is recorded',async()=>{
  const {decisionBody,idKeeper,decisionNotice,moveNotice}=await import('./rm.js');
  assert.deepEqual(decisionBody('d1','accept',{startsAt:'2026-11-09T08:30:00.000Z'}),{decision_id:'d1',decision:'accept',starts_at:'2026-11-09T08:30:00.000Z'});
  assert.deepEqual(decisionBody('d1','decline',{reason:'not_needed',startsAt:'x'}),{decision_id:'d1',decision:'decline',reason:'not_needed'});
  let n=0;const ids=idKeeper(()=>`id${++n}`);
  assert.equal(ids.get('r1'),'id1');assert.equal(ids.get('r1'),'id1');  // double click or retry after a refusal
  assert.equal(ids.get('r2'),'id2');
  ids.done('r1');assert.equal(ids.get('r1'),'id3');  // recorded: a later decision (a lapsed proposal) is a new one
  const meeting={when:'Mon 9 Nov 2026, 4:30 pm',hold_expires:'Tue 10 Nov 2026, 5:00 pm'};
  assert.equal(decisionNotice({replayed:false,request:{client:'Tan Mei Ling',status:'scheduled',meeting}}),
    'Tan Mei Ling’s meeting is confirmed for Mon 9 Nov 2026, 4:30 pm. The client gets an Inbox notice.');
  assert.match(decisionNotice({replayed:true,request:{client:'Tan Mei Ling',status:'countered',meeting}}),/^Already recorded\. You proposed .* until Tue 10 Nov 2026, 5:00 pm\.$/);
  assert.match(decisionNotice({request:{client:'X',status:'declined',decline_reason:'redirected'}}),/\(Passed to the team that handles it\)/);
  assert.equal(decisionNotice(null),'');
  assert.equal(moveNotice({replayed:false,when:'Thu 12 Nov 2026, 3:00 pm'},'Michelle Ng'),'Michelle Ng’s meeting moved to Thu 12 Nov 2026, 3:00 pm. The client gets an Inbox notice.');
});

test('times to propose or move to come from her free half hours, read as Singapore time',async()=>{
  const {freeTimes,sgtInput}=await import('./rm.js');
  const availability={days:[{date:'2026-11-09',free:['16:00','16:30']},{date:'2026-11-10',free:[]},{date:'2026-11-11',free:['09:00']}]};
  const times=freeTimes(availability,['2026-11-09T08:30:00Z']);  // 16:30 SGT is one of the client's own times
  assert.deepEqual(times.map(t=>[t.date,t.time]),[['2026-11-09','16:00'],['2026-11-11','09:00']]);
  assert.equal(times[0].starts_at,'2026-11-09T08:00:00.000Z');
  assert.deepEqual(freeTimes(null),[]);
  assert.equal(sgtInput('2026-11-12T15:00'),'2026-11-12T07:00:00.000Z');
  assert.equal(sgtInput(''),null);assert.equal(sgtInput('12/11/2026 3pm'),null);
});

test('Today hands the Requests tab to Schedule once',async()=>{
  const {openSchedule,takeScheduleTab}=await import('./rm.js');
  globalThis.location??={hash:''};
  openSchedule('requests');
  assert.equal(location.hash,'rm-schedule');
  assert.equal(takeScheduleTab(),'requests');assert.equal(takeScheduleTab(),null);
});
