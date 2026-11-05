import test from 'node:test';
import assert from 'node:assert/strict';
import {chatTopicsNote,insightSentence,line,parseInsightTarget,parseTalkingPoints,sharedTopicRows} from './talkingPoints.js';
import {parseAction} from './actions.js';
import {offerLabels} from './offers.js';
import {consentCategoryText} from './consentData.js';

const id=n=>`12345678-1234-1234-1234-1234567890${String(n).padStart(2,'0')}`;
const point=(extra={})=>({point_id:id(1),topic:'home_loan',stage:'planning',timeframe:'within_12m',concerns:[],origin:'inferred',
  first_seen:'2026-11-05',last_seen:'2026-11-05',visible_from:'2026-11-06T09:00:00+08:00',pending:true,
  evidence:[{quote:'I’m thinking of buying a condo next year',recorded_at:'2026-11-05T09:00:00+08:00'}],...extra});
const withLine=p=>({...p,line:line(p),topic_name:'Home loan'});
const mine=(points=[withLine(point())],extra={})=>({call_id:id(9),as_of:'2026-11-05T09:00:00+08:00',
  data:{points,muted:[],pause:null,chat_topics:true,rm_first_name:'Sophie',...extra}});

test('every topic reads exactly as the RM side renders it (schemas/meetings.TalkingPoint.line)',()=>{
  assert.equal(line(point()),'Home loan · planning · within 12 months · inferred from chat');
  assert.equal(line({...point(),topic:'investments',stage:'exploring',timeframe:'within_3m',concerns:['fees'],origin:'confirmed'}),
    'Investments · exploring · within 3 months · asks about fees · shared by the client');
  assert.equal(line({...point(),timeframe:'unknown',origin:'confirmed'}),'Home loan · planning · shared by the client');
});

test('her view is read strictly: the line must match, quotes are bounded and only list values pass',()=>{
  const view=parseTalkingPoints(mine());
  assert.equal(view.points[0].line,'Home loan · planning · within 12 months · inferred from chat');
  assert.equal(view.points[0].pending,true);
  assert.equal(view.points[0].evidence[0].quote,'I’m thinking of buying a condo next year');
  assert.equal(view.rm_first_name,'Sophie');
  for(const bad of [{...withLine(point()),line:'Home loan · ready'},withLine(point({topic:'savings'})),withLine(point({stage:'keen'})),
    withLine(point({evidence:[{quote:'x'.repeat(201),recorded_at:'2026-11-05T09:00:00+08:00'}]})),withLine(point({concerns:['tax']}))])
    assert.throws(()=>parseTalkingPoints(mine([bad])));
  assert.throws(()=>parseTalkingPoints(mine([withLine(point()),withLine(point())])));  // one row per point
  assert.equal(parseTalkingPoints(mine([],{pause:{pause_id:id(3),until:'2027-01-04T09:00:00+08:00'},muted:['insurance']})).pause.until,'2027-01-04T09:00:00+08:00');
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
  assert.equal(insightSentence('insight.restore',{point_id:id(1),topic:'home_loan'}),'Restored the home loan topic you deleted');
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
    'Let your relationship manager know: Home loan · planning · within 12 months · shared by the client');
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
  assert.equal(chatTopicsNote('Sophie'),'Topics from this chat may be shared with Sophie for your meetings.');
  assert.equal(chatTopicsNote(null),'Topics from this chat may be shared with your relationship manager for your meetings.');
  assert.equal(consentCategoryText(['profile','chat_topics','transaction_insights']),'Basic profile, Topics from your chats, Transaction insights');
});
