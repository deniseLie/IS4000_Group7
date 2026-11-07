import test from 'node:test';
import assert from 'node:assert/strict';
import {askAssistant,briefNotice,briefsToPrepare,contextAfter,dayCells,followupGroups,historyFor,interactionPayload,isRm,missingFields,partialReply,replyText,requestContext,validationNote} from './rm.js';

test('the assistant hands each streamed section on, ignores unknown events and returns the answer',async()=>{
  const part=(task,heading,source)=>({task_id:task,sections:[{heading,items:[{text:'x',kind:'record',source_ids:[source]}]}],sources:[{id:source,title:source}]});
  const events=[['status','Reading…'],['section',JSON.stringify(part('t1','The meeting','selected-appointment'))],['mystery','{}'],
    ['section','not json'],['answer',JSON.stringify({status:'ok',summary:'Brief',sections:[]})],['done','']];
  const body=events.map(([e,d])=>`event: ${e}\ndata: ${d}\n\n`).join('');
  const fetched=globalThis.fetch;
  globalThis.fetch=async()=>new Response(body,{status:200,headers:{'Content-Type':'text/event-stream'}});
  try {
    const statuses=[],parts=[];
    const answer=await askAssistant({message:'Brief me'},'token',s=>statuses.push(s),undefined,p=>parts.push(p));
    assert.deepEqual(statuses,['Reading…']);
    assert.equal(parts.length,1);assert.equal(parts[0].sections[0].heading,'The meeting');
    assert.equal(answer.summary,'Brief');
    assert.equal((await askAssistant({message:'Brief me'},'token')).summary,'Brief');  // the callbacks stay optional
  } finally {globalThis.fetch=fetched;}
  const shown=partialReply([part('t2','Open requests','ticket-1'),part('t1','Snapshot','profile'),part('t1','Worth raising','profile')]);
  assert.deepEqual(shown.sections.map(s=>s.heading),['Snapshot','Worth raising','Open requests']);  // task order, then arrival
  assert.deepEqual(shown.sources.map(s=>s.id),['profile','ticket-1']);
  assert.equal(shown.partial,true);
});

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

test('Today keeps the backend’s rank and shows its reasons; the page never scores (3b)',async()=>{
  const {rankIndex,keyOf,byRank,reasonText,firstThings,nextStep,REPORT_TITLES}=await import('./rm.js');
  const items=[
    {key:'ticket:TCK-001010',kind:'request',customer_id:1002,client:'Michelle Ng',title:'Urgent card',when:'Fri 6 Nov 2026, 12:00 pm',score:51,rank:1,do_first:true,
      parts:[{code:'urgent',points:50,reason:'Urgent request'},{code:'waiting',points:1,reason:'Waiting 1 day'}],ref:{reference:'TCK-001010'}},
    {key:'meeting_request:r1',kind:'meeting_request',customer_id:1007,client:'Tan Mei Ling',title:'Meeting request',when:'Thu 5 Nov 2026, 9:00 am',score:32,rank:2,do_first:true,
      parts:[{code:'respond_by_passed',points:30,reason:'Answer was due Thu 5 Nov 2026, 9:00 am'},{code:'waiting',points:2,reason:'Waiting 2 days'}],ref:{request_id:'r1'}},
    {key:'meeting:a1',kind:'meeting',customer_id:1002,client:'Michelle Ng',title:'Meeting',when:'Sat 7 Nov 2026, 10:00 am',score:20,rank:3,do_first:true,
      parts:[{code:'meeting_2d',points:20,reason:'Meeting in 2 days'}],ref:{appointment_id:'a1'}},
    {key:'review:1002:2026-11-12',kind:'review',customer_id:1002,client:'Michelle Ng',title:'Compliance review',when:'12 Nov 2026',score:20,rank:4,do_first:false,
      parts:[{code:'deadline_14d',points:20,reason:'Review due in 7 days (12 Nov 2026)'}],ref:{}},
    {key:'task:k1:2026-11-20',kind:'task',customer_id:1007,client:'Tan Mei Ling',title:'Your task: Send the form',when:'20 Nov 2026',score:0,rank:5,do_first:false,parts:[],ref:{item_key:'task:k1:2026-11-20'}}];
  const ranked=rankIndex(items);
  assert.deepEqual(byRank([{reference:'TCK-000001'},{reference:'TCK-001010'},{reference:'TCK-000002'}],'ticket',ranked).map(t=>t.reference),
    ['TCK-001010','TCK-000001','TCK-000002']);  // unranked rows keep their order after the ranked ones
  const followups=[{item_key:'task:k1:2026-11-20',kind:'task'},{item_key:'review:c9:2026-11-12',kind:'review_deadline',customer_id:1002,due_on:'2026-11-12'},{item_key:'m',kind:'milestone'}];
  assert.deepEqual(byRank(followups,'followup',ranked).map(f=>f.item_key),['review:c9:2026-11-12','task:k1:2026-11-20','m']);
  assert.equal(keyOf('appointment',{appointment_id:'a1'}),'meeting:a1');assert.equal(keyOf('meeting_request',{request_id:'r1'}),'meeting_request:r1');
  assert.equal(reasonText(items[0]),'Urgent request · Waiting 1 day');assert.equal(reasonText(items[4]),'');assert.equal(reasonText(undefined),'');
  assert.deepEqual(firstThings(items).map(x=>x.key),['ticket:TCK-001010','meeting_request:r1','meeting:a1']);
  assert.deepEqual(nextStep(items[0]),{label:'Draft a reply',message:'Draft a reply to Michelle Ng about TCK-001010',intent:'draft_message',
    context:{client_id:1002,ticket_ref:'TCK-001010'}});
  assert.deepEqual(nextStep(items[1]),{label:'Answer',schedule:'requests'});
  assert.equal(nextStep(items[2]).intent,'meeting_brief');assert.equal(nextStep(items[2]).context.appointment_id,'a1');
  assert.equal(nextStep(items[4]).message,"What do I need to know about Tan Mei Ling's Send the form (due 20 Nov 2026)?");
  assert.equal(REPORT_TITLES.today,'Your day');
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

test('the Calendar agent’s availability card becomes the Schedule page’s calls, in order (3a)',async()=>{
  const {availabilitySteps,availabilityNotice,runStep,REPORT_TITLES}=await import('./rm.js');
  const card={kind:'block',blocks:[{starts_at:'2026-11-06T12:00:00+08:00',ends_at:'2026-11-06T14:00:00+08:00',when:'Fri 6 Nov 2026, 12:00 pm – 2:00 pm'}],
    removals:[{block_id:'b/1',when:'Fri 6 Nov 2026, 9:00 am – 10:00 am'}],hours:[{weekday:1,day:'Monday',starts:'10:00',ends:'18:00',current:'09:00 – 17:00'},
      {weekday:6,day:'Saturday',starts:null,ends:null,current:'10:00 – 12:00'}],kept:[]};
  const steps=availabilitySteps(card);
  assert.deepEqual(steps.map(s=>[s.method,s.path]),[['POST','/availability/blocks'],['DELETE','/availability/blocks/b%2F1'],['PUT','/availability/hours'],['PUT','/availability/hours']]);
  assert.deepEqual(steps[0].body,{starts_at:'2026-11-06T12:00:00+08:00',ends_at:'2026-11-06T14:00:00+08:00',reason:'Unavailable'});
  assert.deepEqual([steps[2].body,steps[3].body],[{weekday:1,starts:'10:00',ends:'18:00'},{weekday:6,starts:null,ends:null}]);
  assert.deepEqual(steps.map(s=>s.label),['Block Fri 6 Nov 2026, 12:00 pm – 2:00 pm','Open Fri 6 Nov 2026, 9:00 am – 10:00 am again',
    'Monday: 10:00 – 18:00 (now 09:00 – 17:00)','Saturday: not working (now 10:00 – 12:00)']);
  assert.deepEqual(availabilitySteps(null),[]);
  assert.match(availabilityNotice({kind:'hours'}),/Working hours saved/);assert.match(availabilityNotice(card),/^Blocked/);
  const sent=[];const real=globalThis.fetch;
  globalThis.fetch=async(url,init)=>{sent.push([init.method,url,init.body?JSON.parse(init.body):null]);return {ok:true,status:200,json:async()=>({})};};
  try{for(const step of steps.slice(0,2))await runStep(step,'token');}finally{globalThis.fetch=real;}
  assert.deepEqual(sent.map(([m,u])=>[m,u.replace(/^.*\/rm/,'')]),[['POST','/availability/blocks'],['DELETE','/availability/blocks/b%2F1']]);
  assert.equal(sent[0][2].reason,'Unavailable');
  assert.equal(REPORT_TITLES.decide_request,'Answer a meeting request');assert.equal(REPORT_TITLES.open,'From your records');
});

test('the ask card sends a reason code, the meeting only for meeting prep, and says nothing is shared yet',async()=>{
  const {askBody,askNotice,askReasons}=await import('./rm.js');
  const card={customer_id:1002,client:'Michelle Ng (DEMO)',category:'transaction_insights',category_label:'Transaction insights',reason:'meeting_prep',
    meeting_id:'00000000-0000-4000-8000-000000000001',meeting_when:'Thu 19 Nov 2026, 10:00 am'};
  assert.deepEqual(askBody('k1',card,'meeting_prep'),{request_key:'k1',customer_id:1002,category:'transaction_insights',reason:'meeting_prep',meeting_id:card.meeting_id});
  assert.equal(askBody('k1',card,'review').meeting_id,null);
  assert.deepEqual(askReasons(card).map(([k])=>k),['meeting_prep','review','advice']);
  assert.deepEqual(askReasons({...card,meeting_id:null}).map(([k])=>k),['review','advice']);  // no meeting: no meeting-prep reason
  assert.equal(askNotice({created:true,replayed:false,ask:{reference:'TCK-000123',lapses_sgt:'Thu 19 Nov 2026, 9:00 am'}},card),
    'Michelle Ng gets an Inbox notice and request TCK-000123. Nothing is shared until they switch it on; the request lapses on Thu 19 Nov 2026, 9:00 am.');
  assert.match(askNotice({created:true,replayed:true,ask:{reference:'TCK-000123',lapses_sgt:'x'}},card),/^Already recorded\. /);
  assert.equal(askNotice({created:false,duplicate:{lapses_sgt:'Fri 20 Nov 2026, 9:00 am'}},card),
    'You already asked Michelle Ng to share transaction insights. The request lapses on Fri 20 Nov 2026, 9:00 am.');
  assert.match(askNotice({created:false,already_shared:true},card),/already shares transaction insights/);
});

test('briefings are prepared for the next 7 days only when missing or stale, soonest first, at most 3 (0118)',()=>{
  const now=new Date('2026-11-07T02:00:00Z'),at=d=>new Date(now.getTime()+d*86400000).toISOString();
  const meetings=[{meeting_id:'late',starts_at:at(9),brief_version:null},{meeting_id:'ready',starts_at:at(1),brief_version:1,stale:false},
    {meeting_id:'stale',starts_at:at(3),brief_version:2,stale:true},{meeting_id:'soon',starts_at:at(2),brief_version:null},{meeting_id:'past',starts_at:at(-1),brief_version:null}];
  assert.deepEqual(briefsToPrepare(meetings,now).map(m=>m.meeting_id),['soon','stale']);
  assert.equal(briefsToPrepare([1,2,3,4].map(d=>({meeting_id:d,starts_at:at(d/2),brief_version:null})),now).length,3);
});

test('a saved briefing says what changed: stale records, withdrawn sharing, removed topics and dropped suggestions (0118)',()=>{
  assert.deepEqual(briefNotice(null),[]);
  assert.deepEqual(briefNotice({stale:false,withdrawn:[],removed_points:[],withheld:0}),[]);
  const lines=briefNotice({stale:true,withdrawn:[{category:'transaction_insights',on:'7 Nov 2026'}],removed_points:[{point_id:'p',on:'7 Nov 2026'}],withheld:2});
  assert.equal(lines.length,4);
  assert.match(lines[1],/stopped sharing transaction insights on 7 Nov 2026/);
  assert.match(lines[2],/removed a topic/);
  assert.match(lines[3],/2 items were left out/);
  assert.equal(briefNotice({withdrawn:[{category:'chat_topics',on:'7 Nov 2026'}],removed_points:[{point_id:'p'}]}).length,1);  // the withdrawal covers it
});

test('the assistant home groups every starter by the moment of the day and suggests one next step per answer',async()=>{
  const {STARTER_GROUPS,nextSteps}=await import('./rm.js');
  assert.deepEqual(STARTER_GROUPS.map(g=>g.label),['Start my day','Before a meeting','After a meeting','Any time']);
  for(const g of STARTER_GROUPS)for(const s of g.starters)assert.ok(s.message&&s.intent||s.draft||s.navigate,s.title);
  assert.equal(nextSteps({status:'ok',intent:'meeting_brief'})[0].navigate,'schedule');
  assert.deepEqual(nextSteps({status:'ok',intent:'today',clarification:{question:'Which?'}}),[]);
  assert.deepEqual(nextSteps({status:'denied',intent:'today'}),[]);
});

test('first-time users see words, short meeting names and each repeated item once',async()=>{
  const {humanize,meetingTitle,groupSame,stageWords}=await import('./rm.js');
  assert.equal(humanize('Client: Michelle Ng. Preferred language: en. Employment: self_employed.'),'Client: Michelle Ng. Preferred language: English. Employment: self employed.');
  assert.equal(humanize('Recorded date: Time deposit ****4410 matures on 11 Nov 2026'),'Time deposit ****4410 matures on 11 Nov 2026');
  assert.equal(meetingTitle('Property financing options',['home_loan']),'Property financing options');
  assert.equal(meetingTitle('I’m thinking of buying a condo next year, can I meet Sophie about property financing?',['home_loan']),'Property financing meeting');
  assert.deepEqual(groupSame([{customer_id:1,title:'Urgent card'},{customer_id:1,title:'Urgent card'},{customer_id:2,title:'Urgent card'}]).map(x=>x.count),[2,1]);
  assert.equal(stageWords('awaiting_transcript','provider'),'Waiting for the Zoom transcript');
});

test('my clients: one sentence per client, the client who needs you most first',async()=>{
  const {clientStatus,byNeed}=await import('./rm.js');
  const michelle={name:'Michelle Ng',urgent:true,open_rm_tickets:14,overdue:37,open_specialist_tickets:24};
  const meiling={name:'Tan Mei Ling (DEMO)',open_rm_tickets:2,overdue:2,open_specialist_tickets:0,inherited_from:'Rachel Goh'};
  assert.deepEqual(clientStatus(michelle),{tone:'urgent',headline:'An urgent request and 13 more waiting for you',detail:'37 requests past the promised date · 24 with bank teams'});
  assert.equal(clientStatus(meiling).detail,'2 requests past the promised date · New to you: read Rachel’s handover brief');
  assert.deepEqual(byNeed([meiling,michelle]).map(c=>c.name),['Michelle Ng','Tan Mei Ling (DEMO)']);
});

test('today: one ranked list, urgent first, meetings to document next, each meeting once, shared topics before the rest',async()=>{
  const {todayList,awarenessList}=await import('./rm.js');
  const priorities=[
    {key:'meeting:m1',kind:'meeting',client:'Michelle Ng',customer_id:1,title:'Property financing options',do_first:true,ref:{appointment_id:'m1'},parts:[{reason:'Meeting in 2 days'}]},
    {key:'ticket:T1',kind:'request',client:'Michelle Ng',customer_id:1,title:'Urgent card',do_first:true,ref:{reference:'T1',urgency:'urgent'},parts:[{reason:'Urgent request'},{reason:'2 days past target'}]},
    {key:'ticket:T2',kind:'request',client:'Michelle Ng',customer_id:1,title:'Urgent card',do_first:true,ref:{reference:'T2',urgency:'urgent'},parts:[]},
    {key:'task:9',kind:'task',client:'Tan Mei Ling',customer_id:2,title:'Your task: send rates',do_first:false,parts:[{reason:'Was due 3 Nov'}]}];
  const reminders=[{reminder_id:'r1',kind:'preparation',meeting_id:'m1',client:'Michelle Ng'},{reminder_id:'r2',kind:'transcript',meeting_id:'m0',client:'Michelle Ng'}];
  const toDocument=[{appointment_id:'m0',client:'Michelle Ng',purpose:'Periodic review',when:'Wed 4 Nov'}];
  const shared=[{share_id:'s1',client:'Tan Mei Ling',customer_id:2,topic:'investments',line:'Exploring investments within 3 months'}];
  const said='[Urgent card] Charge the client does not recognise. Client’s words: “why is there a SGD 3,850 charge?”';
  const tickets=[{reference:'T1',customer_id:1,client:'Michelle Ng',category_label:'Urgent card',summary:said},{reference:'T2',customer_id:1,client:'Michelle Ng',category_label:'Urgent card',summary:said}];
  const list=todayList({priorities,reminders,toDocument,shared,tickets});
  assert.equal(todayList({priorities,reminders,toDocument,shared}).filter(x=>x.key.startsWith('ticket:')).length,2,'without the ticket text nothing is merged');
  assert.deepEqual(list.map(x=>x.key),['ticket:T1','reminder:r2','meeting:m1','shared:s1','task:9']);
  const {todayAction,slashActions,meetingSuggestions}=await import('./rm.js');
  assert.equal(todayAction(list[1]).label,'Add transcript or notes');
  assert.equal(todayAction(list[0]).label,'Draft a reply');
  assert.equal(todayAction(list[4]).label,'Ask the assistant');
  assert.ok(slashActions('/hand').some(a=>a.title==='Handover brief'));assert.deepEqual(slashActions('hi'),[]);
  assert.equal(meetingSuggestions(3,'Michelle Ng').at(-1).label,'What’s still open for Michelle?');
  assert.equal(list[0].why,'2 requests like this from Michelle · 2 days past target');
  assert.equal(list[3].title,'exploring investments within 3 months');
  assert.equal(list[4].title,'send rates');
  const followups=[{item_key:'task:9',kind:'task'},{item_key:'spec:1',kind:'specialist_ticket',owner:'specialist'},{item_key:'c:1',kind:'commitment'}];
  assert.deepEqual(awarenessList(followups,priorities).map(f=>f.item_key),['spec:1','c:1']);
});

test('ticket inbox: only the same client, type and words merge; the oldest leads; fraud and issues are flagged',async()=>{
  const {ticketRows,ticketTitle,ticketIssues,possibleFraud,ticketParts,duplicateNote}=await import('./rm.js');
  const card='[Urgent card] Charge the client does not recognise. Cards on record: card ending 7310 (active), card ending 5592 (active). Client’s words: “eh why is there a SGD 3,850 charge in London?? my transfer to lim interiors still says pending, also i got a weird OTP last night and now my app is locked”';
  const fee=`[Urgent card] Charge the client does not recognise. Cards on record: card ending 7310 (active). Client’s words: “I want to talk to my RM about my annual fee, and also there's a SGD 3,850 charge in London I don't recognise”`;
  const t=(reference,summary,age,extra={})=>({reference,summary,age_hours:age,customer_id:1002,client:'Michelle Ng',category_label:'Question for your relationship manager',urgency:'urgent',overdue:true,...extra});
  const rows=ticketRows([t('TCK-001082',card,40),t('TCK-001079',card.replace('EH WHY','eh why'),50),t('TCK-001080',fee,49),t('TCK-9',  'Statement copy for October',2,{urgency:'normal',overdue:false})]);
  assert.deepEqual(rows.map(r=>[r.lead.reference,r.count]),[['TCK-001079',2],['TCK-001080',1],['TCK-9',1]]);
  assert.equal(rows[0].same[0].reference,'TCK-001082');
  assert.equal(ticketTitle(rows[0].lead),'charge she doesn’t recognise · SGD 3,850');
  assert.deepEqual(ticketIssues(rows[0].lead),['Card charge','Transfer pending','Unexpected OTP','App locked']);
  assert.ok(possibleFraud(rows[0].lead));assert.ok(!possibleFraud(rows[1].lead));
  assert.deepEqual(ticketIssues(rows[1].lead),['Annual fee','Card charge']);
  assert.equal(ticketTitle(rows[1].lead),'annual fee · also a charge she doesn’t recognise (SGD 3,850)');
  assert.equal(ticketParts(card).cards,'card ending 7310 (active), card ending 5592 (active)');
  assert.equal(ticketTitle(rows[2].lead),'statement copy for October');
  assert.equal(duplicateNote(rows[0].lead),'We have answered this under TCK-001079. Please see that request for our reply.');
});
