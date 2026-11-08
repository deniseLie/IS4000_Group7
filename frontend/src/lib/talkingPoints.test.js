import test from 'node:test';
import assert from 'node:assert/strict';
import {chatTopicsNote,insightSentence,line,parseInsightTarget,parseTalkingPoints,plainLine,sharedTopicRows} from './talkingPoints.js';
import {parseAction} from './actions.js';
import {offerLabels} from './offers.js';
import {consentCategoryText} from './consentData.js';

const id=n=>`12345678-1234-1234-1234-1234567890${String(n).padStart(2,'0')}`;
const point=(extra={})=>({point_id:id(1),topic:'home_loan',stage:'planning',timeframe:'within_12m',concerns:[],origin:'inferred',
  first_seen:'2026-11-05',last_seen:'2026-11-05',visible_from:'2026-11-06T09:00:00+08:00',pending:true,...extra});
const withLine=p=>({...p,line:line(p),topic_name:'Property financing'});
const mine=(points=[withLine(point())],extra={})=>({call_id:id(9),as_of:'2026-11-05T09:00:00+08:00',
  data:{points,muted:[],pause:null,chat_topics:true,rm_first_name:'Sophie',...extra}});

test('every topic reads exactly as the RM side renders it (schemas/meetings.TalkingPoint.line)',()=>{
  assert.equal(line(point()),'Property financing · planning · within 12 months · inferred from chat');
  assert.equal(line({...point(),topic:'investments',stage:'exploring',timeframe:'within_3m',concerns:['fees'],origin:'confirmed'}),
    'Investments · exploring · within 3 months · asks about fees · shared by the client');
  assert.equal(line({...point(),timeframe:'unknown',origin:'confirmed'}),'Property financing · planning · shared by the client');
  assert.equal(line(point({kind:'hdb_resale',concerns:['lock_in','rates']})),  // 0143: the kind after the stage, concerns in words
    'Property financing · planning · HDB resale · within 12 months · asks about lock-in, rates · inferred from chat');
  assert.equal(line(point({topic:'fixed_deposit',kind:'renewal',timeframe:'unknown',concerns:['down_payment']})),
    'Time deposit · planning · renewal · asks about down payment · inferred from chat');
});

test('her view is read strictly: the line must match, only list values pass and no words are kept',()=>{
  const view=parseTalkingPoints(mine());
  assert.equal(view.points[0].line,'Property financing · planning · within 12 months · inferred from chat');
  assert.equal(view.points[0].pending,true);
  assert.equal(view.rm_first_name,'Sophie');
  const old=parseTalkingPoints(mine([withLine(point({evidence:[{quote:'I’m thinking of buying a condo next year',recorded_at:'2026-11-05T09:00:00+08:00'}]}))]));
  assert.equal('evidence' in old.points[0],false);  // a server before 0142: her words are dropped, never shown
  for(const bad of [{...withLine(point()),line:'Property financing · ready'},withLine(point({topic:'savings'})),withLine(point({stage:'keen'})),
    withLine(point({concerns:['tax']})),withLine(point({kind:'etf'})),withLine(point({kind:'condo'})),withLine(point({on_hold:'yes'}))])
    assert.throws(()=>parseTalkingPoints(mine([bad])));  // a kind of another topic, an unlisted kind, a non-boolean on hold
  assert.throws(()=>parseTalkingPoints(mine([withLine(point()),withLine(point())])));  // one row per point
  assert.deepEqual([view.points[0].kind,view.points[0].on_hold,view.points[0].on_hold_at],[null,false,null]);  // a server before 0143
  const held=parseTalkingPoints(mine([withLine(point({kind:'hdb_resale',concerns:['lock_in'],pending:false,on_hold:true,on_hold_at:'2026-11-08T10:00:00+08:00'}))])).points[0];
  assert.deepEqual([held.kind,held.on_hold,held.on_hold_at,held.concerns],['hdb_resale',true,'2026-11-08T10:00:00+08:00',['lock_in']]);
  assert.equal(parseTalkingPoints(mine([],{pause:{pause_id:id(3),until:'2027-01-04T09:00:00+08:00'},muted:['insurance']})).pause.until,'2027-01-04T09:00:00+08:00');
});

test('she reads each topic in plain words with the same facts as the RM line',()=>{
  assert.equal(plainLine(point()),"You're planning to finance a property in the next 12 months.");
  assert.equal(plainLine(point({stage:'curious',timeframe:'unknown'})),'You asked about property financing.');
  assert.equal(plainLine(point({topic:'fixed_deposit',stage:'curious',timeframe:'unknown',concerns:['rates']})),'You asked about time deposits. You mentioned rates.');
  assert.equal(plainLine(point({kind:'hdb_resale',concerns:['lock_in','rates']})),
    "You're planning to finance a property (HDB resale) in the next 12 months. You mentioned lock-in and rates.");
  assert.equal(plainLine(point({topic:'investments',stage:'exploring',timeframe:'within_3m',concerns:['fees','rates','timing']})),
    "You're looking into investing, with the next 3 months in mind. You mentioned fees, rates and timing.");
  assert.equal(plainLine(point({topic:'fixed_deposit',stage:'ready',timeframe:'now'})),"You're ready to place a time deposit now.");
  assert.equal(plainLine(point({topic:'insurance',stage:'concern',timeframe:'unknown',concerns:['fees']})),'You raised a concern about your insurance. You mentioned fees.');
  assert.equal(plainLine(point({topic:'investments',stage:'planning',timeframe:'later'})),"You're planning to invest later on.");
  for(const topic of ['home_loan','investments','fixed_deposit','insurance'])for(const stage of ['curious','exploring','planning','ready','concern'])
    assert.doesNotMatch(plainLine(point({topic,stage})),/undefined|·|curious|exploring|inferred/);  // no codes, no RM wording
});

function insight(type,target,result,state='succeeded') {
  const command_id=id(20),case_id=id(21);
  return {command:{command_id,case_id,customer_id:1002,type,target,idempotency_key:`${case_id}:talking-points:${type}`,state,
    proposed_at:'2026-11-05T01:00:00Z',expires_at:'2026-11-05T01:30:00Z',confirmed_at:type==='insight.share'?'2026-11-05T01:01:00Z':null},
    receipt:state==='succeeded'?{receipt_id:`rcpt_${command_id.replaceAll('-','')}`,command_id,customer_id:1002,outcome:'succeeded',result,audit_event_id:7,created_at:'2026-11-05T01:01:00Z'}:null};
}

test('insight actions parse on the ledger with matching receipts only',()=>{
  const deleted=parseAction(insight('insight.delete',{point_id:id(1),topic:'home_loan'},{point_id:id(1),topic:'home_loan',status:'deleted'}));
  assert.deepEqual(deleted.receipt.result,{point_id:id(1),topic:'home_loan',status:'deleted',restore_until:null});
  const undo=parseAction(insight('insight.delete',{point_id:id(1)},{point_id:id(1),topic:'home_loan',status:'deleted',restore_until:'2026-11-12T09:00:00+08:00'}));
  assert.equal(undo.receipt.result.restore_until,'2026-11-12T09:00:00+08:00');  // 0110: Undo for 7 days
  assert.deepEqual(parseAction(insight('insight.restore',{point_id:id(1)},{point_id:id(1),topic:'home_loan',status:'restored'})).receipt.result,
    {point_id:id(1),topic:'home_loan',status:'restored'});
  assert.throws(()=>parseAction(insight('insight.restore',{point_id:id(1)},{point_id:id(1),topic:'home_loan',status:'deleted'})));
  assert.equal(insightSentence('insight.restore',{point_id:id(1),topic:'home_loan'}),'Restored the property financing topic you deleted');
  assert.throws(()=>parseAction(insight('insight.delete',{point_id:id(1),topic:'home_loan'},{point_id:id(2),topic:'home_loan',status:'deleted'})));
  assert.equal(parseAction(insight('insight.mute',{topic:'insurance'},{topic:'insurance',status:'muted',removed:1})).receipt.result.removed,1);
  assert.throws(()=>parseAction(insight('insight.mute',{topic:'insurance'},{topic:'home_loan',status:'muted',removed:0})));
  assert.equal(parseAction(insight('insight.resume',{topic:null},{topic:null,status:'resumed'})).receipt.result.topic,null);
  const share=insight('insight.share',{topic:'home_loan',stage:'planning',timeframe:'within_12m',advisor_id:id(5)},
    {point_id:id(1),topic:'home_loan',stage:'planning',timeframe:'within_12m',origin:'confirmed',notified:true});
  assert.equal(parseAction(share).receipt.result.notified,true);
  assert.throws(()=>parseAction({...share,command:{...share.command,confirmed_at:null}}));  // shared only after her yes
  assert.throws(()=>parseInsightTarget('insight.share',{topic:'home_loan',stage:'planning',note:undefined,timeframe:'soon'}));
  assert.equal(insightSentence('insight.share',{topic:'home_loan',stage:'planning',timeframe:'within_12m'}),
    'Let your relationship manager know: Property financing · planning · within 12 months · shared by the client');
  assert.equal(insightSentence('insight.mute',{topic:'insurance'}),'Stopped suggesting insurance as a topic');
});

test('a Let-her-know offer has its own button words; any other offer keeps Confirm and Cancel',()=>{
  const offer=type=>({offers:[{command_id:id(1),type,summary:'x',expires_at:'2026-11-05T01:30:00Z'}]});
  assert.deepEqual(offerLabels(offer('insight.share')),{confirm:'Let her know',cancel:'Not now'});
  assert.deepEqual(offerLabels(offer('meeting.request')),{confirm:'Confirm',cancel:'Cancel'});
  assert.deepEqual(offerLabels({offers:[...offer('insight.share').offers,...offer('handoff.create').offers]}),{confirm:'Confirm',cancel:'Cancel'});
  assert.deepEqual(offerLabels(null),{confirm:'Confirm',cancel:'Cancel'});
});

test('RM Today lists only confirmed topics whose line matches, newest notice marked new',()=>{
  const shared={share_id:id(6),customer_id:1007,client:'Mei Ling Tan',notice:'Mei Ling Tan shared a topic with you. Open your workspace.',
    shared_at:'2026-11-05T00:00:00Z',shared_text:'Thu 5 Nov, 8:00 am',point:{...point(),topic:'investments',stage:'exploring',timeframe:'within_3m',origin:'confirmed'}};
  shared.line=line(shared.point);
  const [row]=sharedTopicRows([shared,{...shared,share_id:id(7),point:{...shared.point,origin:'inferred'}},{...shared,share_id:id(8),line:'Investments'}],Date.parse('2026-11-05T06:00:00Z'));
  assert.deepEqual(row,{share_id:id(6),customer_id:1007,client:'Mei Ling Tan',topic:'investments',line:'Investments · exploring · within 3 months · shared by the client',
    shared_text:'Thu 5 Nov, 8:00 am',notice:shared.notice,isNew:true});
  assert.equal(sharedTopicRows([shared]).length,1);
  assert.deepEqual(sharedTopicRows(null),[]);
});

test('the chat line and the consent history read in plain words',()=>{
  assert.equal(chatTopicsNote('Sophie'),'Insights from this chat may be shared with Sophie for your meetings.');
  assert.equal(chatTopicsNote(null),'Insights from this chat may be shared with your relationship manager for your meetings.');
  assert.equal(consentCategoryText(['profile','chat_topics','transaction_insights']),'Basic profile, Chat insights, Transaction insights');
});
