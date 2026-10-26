import test from 'node:test';
import assert from 'node:assert/strict';
import {coveringGrant,meetingDays,noteLines,organiseAgenda,parseHandoverDraft,parseIntroduction,parseMeetingOptions,recentChange,relationshipIntent,requestProgress,splitNote} from './relationshipWorkflow.js';

const RM='00000000-0000-4000-8000-100300000002';
const CALL='00000000-0000-4000-8000-000000000001';
function section(anchor) {return {cite:`kb:rm-change@v1#${anchor}`,article_id:'rm-change',version:1,anchor,title:'Changes to your relationship manager',heading:'Heading',body:'Approved text.',publisher:'DemoBank coursework project'};}
function intro(data) {return {call_id:CALL,as_of:'2026-10-26T09:00:00+08:00',data};}
const current={rm_id:RM,name:'Marcus Chua (DEMO)',role:'Senior Relationship Manager',team:'Private Clients Team B',contact:{email:'marcus.chua@demobank.example',phone:'+65 6000 0000'},started_on:'2026-10-15'};
const previous={name:'Rachel Goh (DEMO)',role:'Relationship Manager',team:'Private Clients Team A',started_on:'2024-03-01',ended_on:'2026-10-14'};

test('David\'s Workflow 3 messages map to the matching in-chat step',()=>{
  assert.equal(relationshipIntent('why did my old guy leave? will things stay the same?'),'change');
  assert.equal(relationshipIntent('can someone send me my annual portfolio statement'),'request');
  assert.equal(relationshipIntent('whats happening with my requests?'),'status');
  assert.equal(relationshipIntent('can i meet her next week'),'meeting');
  assert.equal(relationshipIntent('Prepare handover note'),'note');
  assert.equal(relationshipIntent('Who is my relationship manager?'),'intro');
  assert.equal(relationshipIntent('what is my balance'),null);
  assert.equal(relationshipIntent(null),null);
});

test('topics become a short ordered agenda in the customer\'s own words',()=>{
  assert.deepEqual(organiseAgenda('estate planning, review of fees, how often we meet'),['Estate planning','Review of fees','How often we meet']);
  assert.deepEqual(organiseAgenda('estate planning and review of fees\n- Estate planning\n1. how often we meet.'),['Estate planning','Review of fees','How often we meet']);
  assert.equal(organiseAgenda(Array.from({length:20},(_,i)=>`topic ${i}`).join(',')).length,12);
  assert.deepEqual(organiseAgenda(' , ;'),[]);
  const lines=noteLines(['Preference: English','Agenda item 1: Old'],['Estate planning','Review of fees']);
  assert.deepEqual(lines,['Preference: English','Agenda item 1: Estate planning','Agenda item 2: Review of fees']);
  assert.deepEqual(splitNote(lines),{drafted:['Preference: English'],agenda:['Estate planning','Review of fees']});
  assert.equal(noteLines(Array.from({length:10},(_,i)=>`Line ${i}`),['a','b','c','d']).length,12);
});

test('"next week" means next Monday to Friday in Singapore time',()=>{
  // Monday 26 Oct 2026, 16:47 in Singapore (08:47Z).
  assert.deepEqual(meetingDays('can i meet her next week',new Date('2026-10-26T08:47:00Z')),['2026-11-02','2026-11-03','2026-11-04','2026-11-05','2026-11-06']);
  // Sunday 1 Nov 2026 at 23:30 Singapore is still Sunday there: next week starts the following day.
  assert.deepEqual(meetingDays('next week please',new Date('2026-11-01T15:30:00Z'))[0],'2026-11-02');
  assert.deepEqual(meetingDays('tomorrow',new Date('2026-10-26T08:47:00Z')),['2026-10-27']);
  assert.deepEqual(meetingDays('book a meeting',new Date('2026-10-26T08:47:00Z')),['2026-10-26','2026-10-27','2026-10-28','2026-10-29','2026-10-30']);
});

test('request progress is sent → seen → answered and never implies more',()=>{
  assert.deepEqual(requestProgress('sent'),{done:1,label:'Sent'});
  assert.deepEqual(requestProgress('seen'),{done:2,label:'Seen'});
  assert.deepEqual(requestProgress('answered'),{done:3,label:'Answered'});
  assert.deepEqual(requestProgress('closed'),{done:3,label:'Closed'});
});

test('introduction requires the approved wording that matches the recorded change',()=>{
  const parsed=parseIntroduction(intro({current,previous,notice:[section('team-restructure'),section('continuity')]}));
  assert.equal(parsed.current.name,'Marcus Chua (DEMO)');
  assert.equal(parsed.notice[0].url,'/knowledge/sections/rm-change/1/team-restructure');
  assert.equal(recentChange(parsed,new Date('2026-10-26T08:47:00Z')),true);
  assert.equal(recentChange(parsed,new Date('2027-02-01T00:00:00Z')),false);
  assert.equal(parseIntroduction(intro({current:null,previous:null,notice:[section('no-advisor')]})).current,null);
  assert.equal(recentChange(parseIntroduction(intro({current,previous:null,notice:[]}))),false);
  for(const notice of [[],[section('continuity')],[section('team-restructure')],[section('continuity'),section('team-restructure')]])
    assert.throws(()=>parseIntroduction(intro({current,previous,notice})));
  assert.throws(()=>parseIntroduction(intro({current:null,previous:null,notice:[]})));
  assert.throws(()=>parseIntroduction(intro({current:{...current,rm_id:'x'},previous,notice:[section('unrecorded'),section('continuity')]})));
});

test('meeting options and handover drafts reject unverified records',()=>{
  const options=parseMeetingOptions({call_id:CALL,as_of:'2026-10-26T09:00:00Z',data:[{advisor_id:RM,format:'video',joining_details:'Room 1'}]});
  assert.equal(options.options.video.joining_details,'Room 1');
  assert.throws(()=>parseMeetingOptions({call_id:CALL,as_of:'2026-10-26T09:00:00Z',data:[{advisor_id:RM,format:'video',joining_details:'a'},{advisor_id:RM,format:'video',joining_details:'b'}]}));
  assert.throws(()=>parseMeetingOptions({call_id:CALL,as_of:'2026-10-26T09:00:00Z',data:[{advisor_id:RM,format:'fax',joining_details:'a'}]}));
  const draft={advisor_id:RM,recipient_name:'Marcus Chua (DEMO)',profile_fields:['name'],topics:['Preference: English'],sources:[{tool:'requests.list',call_id:CALL,as_of:'2026-10-26T09:00:00Z'}]};
  assert.deepEqual(parseHandoverDraft(draft).topics,['Preference: English']);
  for(const change of [{topics:[]},{profile_fields:['income']},{sources:[]},{advisor_id:'bad'}])assert.throws(()=>parseHandoverDraft({...draft,...change}));
});

test('only an active, unexpired grant for the same recipient covering every category is reused',()=>{
  const grant={grant_id:CALL,active:true,revoked_at:null,purpose:'rm_brief',recipient:RM,granted_at:'2026-10-26T00:00:00Z',expires_at:'2026-11-26T00:00:00Z',categories:['agenda','profile']};
  const now=Date.parse('2026-10-27T00:00:00Z');
  assert.equal(coveringGrant({grants:[grant]},RM,['agenda','profile'],now),grant);
  for(const changed of [{active:false},{revoked_at:'2026-10-26T01:00:00Z'},{recipient:CALL},{categories:['agenda']},{expires_at:'2026-10-26T12:00:00Z'},{purpose:'other'}])
    assert.equal(coveringGrant({grants:[{...grant,...changed}]},RM,['agenda','profile'],now),null);
});
