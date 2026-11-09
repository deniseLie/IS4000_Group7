import test from 'node:test';
import assert from 'node:assert/strict';
import {parseAction} from './actions.js';
import {LIMITS,TOPICS,meetingRequestTarget,parseMeetingDraft,slotLabel,openCount,parseMeetingRequests,requestAppointmentIds,requestChoices,suggestedTime,toggleTime,toggleTopic} from './meetingRequests.js';

const RM='00000000-0000-4000-8000-100200000002',REQUEST='12345678-1234-1234-1234-123456789020',HELD='12345678-1234-1234-1234-123456789021';
const T1='2026-11-10T02:00:00Z',T2='2026-11-09T06:30:00Z',T3='2026-11-11T02:00:00Z',T4='2026-11-12T02:00:00Z';
const row=(extra={})=>({request_id:REQUEST,purpose:'Refinancing my home loan',topics:['home_loan','term_sheet'],format:'video',status:'requested',
  respond_by:'2026-11-06T09:42:00Z',preferred_times:[T2,T1],decline_reason:null,appointment_id:null,created_at:'2026-11-05T01:00:00Z',...extra});
const envelope=data=>({call_id:'12345678-1234-1234-1234-123456789030',as_of:'2026-11-05T01:00:00Z',data});

test('the form picks up to three times and five fixed topics, and builds the request SQL expects', () => {
  let times=[];
  for(const time of [T1,T2,T3,T4])times=toggleTime(times,time);
  assert.deepEqual(times,[T2,T1,T3]);  // the fourth is ignored, the rest are in time order
  assert.deepEqual(toggleTime(times,T1),[T2,T3]);
  let topics=[];
  for(const topic of [...Object.keys(TOPICS),'made_up'])topics=toggleTopic(topics,topic);
  assert.equal(topics.length,LIMITS.topics);
  assert.deepEqual(toggleTopic(['investments'],'investments'),[]);
  const target=meetingRequestTarget({advisorId:RM,purpose:'  Refinancing   my home loan  ',topics:['term_sheet','home_loan'],format:'phone',times:[T1,T2]});
  assert.deepEqual(target,{advisor_id:RM,purpose:'Refinancing my home loan',topics:['home_loan','term_sheet'],format:'phone',preferred_times:[T2,T1]});
  assert.equal(meetingRequestTarget({advisorId:RM,purpose:'card 4111 1111 1111 1111',topics:['card_review'],format:'video',times:[T1]}).purpose,'card [private detail removed]');
});

test('the form explains what is missing before anything is sent', () => {
  const good={advisorId:RM,purpose:'Investments',topics:['investments'],format:'video',times:[T1]};
  for(const [change,message] of [[{purpose:'  '},/what you’d like to discuss/],[{purpose:'x'.repeat(201)},/200 characters/],[{topics:[]},/at least one topic/],
    [{times:[]},/at least one time/],[{times:[T1,T2,T3,T4]},/up to 3 times/],[{format:'chat'},/how you’d like to meet/],[{advisorId:null},/relationship manager/]])
    assert.throws(()=>meetingRequestTarget({...good,...change}),message);
});

test('her requests are parsed newest first, with what she can do on each', () => {
  const older=row({request_id:'12345678-1234-1234-1234-123456789022',status:'countered',appointment_id:HELD,created_at:'2026-11-01T01:00:00Z'});
  const parsed=parseMeetingRequests(envelope([older,row()]));
  assert.deepEqual(parsed.data.map(item=>item.status),['requested','countered']);
  assert.equal(openCount(parsed.data),2);
  assert.deepEqual(requestChoices(parsed.data[0]),['withdraw']);
  assert.deepEqual(requestChoices(parsed.data[1]),['accept','decline','withdraw']);
  assert.deepEqual(requestChoices(row({status:'declined',decline_reason:'not_needed'})),[]);
  const held={appointment_id:HELD,status:'held',starts_at:T3,ends_at:'2026-11-11T02:30:00Z',hold_expires_at:'2026-11-07T01:00:00Z'};
  assert.equal(suggestedTime(parsed.data[1],[held]),held);
  assert.equal(suggestedTime(parsed.data[0],[held]),null);
  assert.deepEqual([...requestAppointmentIds(parsed.data)],[HELD]);
  for(const bad of [row({status:'booked'}),row({topics:['crypto']}),row({preferred_times:[T1,T2,T3,T4]}),row({decline_reason:'busy'}),row({purpose:''})])
    assert.throws(()=>parseMeetingRequests(envelope([bad])));
  assert.throws(()=>parseMeetingRequests(envelope([row(),row()])));
});

function action(type,target,result) {
  const command_id='12345678-1234-1234-1234-123456789012',case_id='12345678-1234-1234-1234-123456789013';
  return {command:{command_id,case_id,customer_id:1002,type,target,idempotency_key:`${case_id}:meetings:${type}`,state:'succeeded',
    proposed_at:'2026-11-05T01:00:00Z',expires_at:'2026-11-05T01:30:00Z',confirmed_at:'2026-11-05T01:01:00Z'},
    receipt:{receipt_id:`rcpt_${command_id.replaceAll('-','')}`,command_id,customer_id:1002,outcome:'succeeded',result,audit_event_id:9,created_at:'2026-11-05T01:01:00Z'}};
}

test('meeting actions are verified on the ledger (Activity and the Meetings tab)', () => {
  const sent=parseAction(action('meeting.request',{advisor_id:RM,purpose:'Home loan',topics:['home_loan'],format:'video',preferred_times:[T2,T1]},
    {request_id:REQUEST,status:'requested',respond_by:'2026-11-06T09:42:00Z',respond_by_text:'Fri 6 Nov 2026, 5:42 pm'}));
  assert.equal(sent.receipt.result.respond_by_text,'Fri 6 Nov 2026, 5:42 pm');
  const accepted=parseAction(action('meeting.counter_reply',{request_id:REQUEST,reply:'accept',advisor_id:RM},
    {request_id:REQUEST,status:'scheduled',reply:'accept',appointment_id:HELD,starts_at:T3,when:'Wed 11 Nov 2026, 10:00 am'}));
  assert.equal(accepted.receipt.result.appointment_id,HELD);
  assert.equal(parseAction(action('meeting.withdraw',{request_id:REQUEST,advisor_id:RM},{request_id:REQUEST,status:'withdrawn'})).receipt.result.status,'withdrawn');
  for(const bad of [
    action('meeting.withdraw',{request_id:REQUEST},{request_id:HELD,status:'withdrawn'}),
    action('meeting.counter_reply',{request_id:REQUEST,reply:'decline'},{request_id:REQUEST,status:'scheduled',reply:'accept',appointment_id:HELD,starts_at:T3}),
    action('meeting.request',{advisor_id:RM,purpose:'Home loan',topics:['crypto'],format:'video',preferred_times:[T1]},{request_id:REQUEST,status:'requested',respond_by:T1,respond_by_text:'x'}),
    {...sent,command:{...action('meeting.request',{advisor_id:RM,purpose:'x',topics:['other'],format:'video',preferred_times:[T1]},{}).command,confirmed_at:null}},
  ])assert.throws(()=>parseAction(bad));
});

test('the chat meeting request draft is read back into fields',()=>{
  const text='I can send your relationship manager, Sophie Lim, this meeting request:\n• What it’s about: “I’m thinking of buying a home.”\n• Topics: Property financing, Investments\n• Format: video call\n• Times that suit you (Singapore time): Mon 16 Nov, 9:00 am; Tue 17 Nov, 9:00 am\nThey accept one of these times…';
  const draft=parseMeetingDraft(text);
  assert.equal(draft.rm,'Sophie Lim');assert.equal(draft.purpose,'I’m thinking of buying a home.');
  assert.equal(parseMeetingDraft(text.replace('a home.”','a home. Can I meet my relationship manager?”')).purpose,'I’m thinking of buying a home.');
  assert.equal(parseMeetingDraft(text.replace('I’m thinking of buying a home.','Can I meet my RM?')).purpose,'Can I meet my RM?');
  assert.deepEqual(draft.topics,['home_loan','investments']);assert.equal(draft.format,'video');
  assert.deepEqual(draft.times,['Mon 16 Nov, 9:00 am','Tue 17 Nov, 9:00 am']);
  assert.equal(parseMeetingDraft('Your balance is SGD 10.'),null);
  assert.equal(slotLabel('2026-11-16T01:00:00Z'),'Mon 16 Nov, 9:00 am');
  assert.equal(slotLabel('2026-11-17T06:30:00Z'),'Tue 17 Nov, 2:30 pm');
});
