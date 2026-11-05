import test from 'node:test';
import assert from 'node:assert/strict';
import {DELETE_ALL,accessLine,alwaysShared,askLine,cardState,choiceSummary,defaultChoice,grantSentence,grantTarget,latestEnd,openAsks,
  parseAccessLog,parseAskResult,parseAskTarget,parseAsks,parseGrantV2Result,parseGrantV2Target,parseSharing,revokeConsequence,scopeText,untilText} from './consentSharing.js';
import {parseAction} from './actions.js';
import {included,offerReply,parseOffer,required,switches} from './offers.js';

const id=n=>`12345678-1234-1234-1234-1234567890${String(n).padStart(2,'0')}`;
const SOPHIE=id(2),day=v=>v.slice(0,10);
const grant=(extra={})=>({grant_id:id(41),legacy:false,scope:{topic_areas:['home_property','investments']},until:'date',basis:'consent',
  granted_at:'2026-11-04T09:00:00+08:00',expires_at:'2027-05-03T09:00:00+08:00',until_meeting_id:null,until_request_id:null,notice_version:'ct-2026-11',renewal_due:false,...extra});
const category=(code,extra={})=>({code,label:{profile_basics:'Profile basics',transaction_insights:'Transaction insights',chat_topics:'Chat topics',
  meeting_recording:'Meeting recording'}[code],description:'What it covers.',gated:code!=='profile_basics',default_on:code==='profile_basics',
  withdrawable:['transaction_insights','chat_topics'].includes(code),max_months:['transaction_insights','chat_topics'].includes(code)?12:null,
  notice_version:{transaction_insights:'ti-2026-11',chat_topics:'ct-2026-11',meeting_recording:'mr-2026-11'}[code]??null,
  reason:code==='profile_basics'?'Your RM needs it to look after you.':null,grant:null,...extra});
const sharing=(extra={})=>({call_id:id(9),as_of:'2026-11-05T09:00:00+08:00',data:{rm:{advisor_id:SOPHIE,name:'Sophie Lim'},
  categories:[category('profile_basics'),category('transaction_insights'),category('chat_topics',{grant:grant()}),
    category('meeting_recording',{grant:grant({grant_id:id(43),scope:{},until:null,basis:'notice',expires_at:null,notice_version:'mr-2026-11'})})],
  accounts:[{account_id:10021,type:'Multi-currency',number:'••••0021',currency:'SGD'}],next_meeting:{meeting_id:id(7),starts_at:'2026-11-12T10:00:00+08:00'},
  topic_areas:['home_property','investments','deposits','insurance','cards','everyday_banking'],today:'2026-11-05',...extra}});

test('Data sharing is read strictly: one card per category, her accounts and next meeting',()=>{
  const view=parseSharing(sharing());
  assert.deepEqual(view.categories.map(c=>[c.code,cardState(c).label]),[['profile_basics','Always shared'],['transaction_insights','Off'],['chat_topics','On'],
    ['meeting_recording','On for every meeting']]);
  assert.deepEqual(alwaysShared(view).map(c=>c.code),['profile_basics']);
  assert.equal(scopeText('chat_topics',view.categories[2].grant.scope),'Home and property, Investments');
  assert.equal(scopeText('transaction_insights',{accounts:[10021],lookback_months:3},view.accounts),'Multi-currency ••••0021 (no card spending) · last 3 months');
  assert.equal(untilText(view.categories[2].grant,day),'Until 2027-05-03');
  assert.equal(untilText(view.categories[3].grant,day),'Under the meeting notice');
  for(const bad of [{rm:{advisor_id:'x',name:'Sophie'}},{categories:[]},{today:'5 Nov'},{categories:[category('chat_topics',{grant:grant({scope:{topic_areas:['health']}})})]},
    {categories:[category('chat_topics',{grant:grant({until:'forever'})})]}])
    assert.throws(()=>parseSharing(sharing(bad)),/could not be verified/);
});

test('a switch sends the scope and end she chose, never past the limit and never until a request outside chat',()=>{
  const view=parseSharing(sharing()),[,insights,topics]=view.categories;
  const choice=defaultChoice(insights,view);
  assert.deepEqual(choice,{accounts:'all',lookback_months:6,until:'date',expires_on:'2027-02-03'});
  assert.deepEqual(grantTarget(insights,choice,view),{category:'transaction_insights',recipient:SOPHIE,scope:{accounts:'all',lookback_months:6},
    notice_version:'ti-2026-11',until:'date',expires_on:'2027-02-03'});
  assert.deepEqual(grantTarget(insights,{accounts:[10021],lookback_months:12,until:'meeting'},view).meeting_id,id(7));
  assert.deepEqual(grantTarget(topics,{topic_areas:['investments','home_property'],until:'date',expires_on:'2026-12-01'},view).scope,{topic_areas:['home_property','investments']});
  assert.throws(()=>grantTarget(topics,{topic_areas:[],until:'date',expires_on:'2026-12-01'},view),/at least one topic area/);
  assert.throws(()=>grantTarget(insights,{until:'request'},view),/could not be verified/);
  assert.throws(()=>grantTarget(insights,{until:'meeting'},{...view,next_meeting:null}),/no confirmed meeting/);
  assert.equal(latestEnd('2026-11-05',12),'2027-11-05');
  assert.deepEqual(defaultChoice(topics,view).topic_areas,['home_property','investments']);  // her current choice
  assert.equal(choiceSummary(insights,choice,view,day),'Sophie Lim will see your transaction insights (All your accounts and cards · last 6 months) until 2027-02-03.');
  assert.match(revokeConsequence('chat_topics','Sophie'),/^Sophie stops seeing topics picked up from your chats, and they are removed at once/);
});

test('the access log and her RM’s asks are ids, codes and dates; the words come from here',()=>{
  const log=parseAccessLog({call_id:id(9),as_of:'2026-11-05T09:00:00+08:00',data:[
    {call_id:id(10),at:'2026-11-05T08:00:00+08:00',advisor_id:SOPHIE,relation:'current_rm',tool:'rm.transaction_insights',category:'transaction_insights',
      purpose:'meeting_prep',meeting_id:id(7),outcome:'read',basis:'consent',grant_id:id(42)},
    {call_id:null,at:'2026-11-05T07:00:00+08:00',advisor_id:id(3),relation:'not_your_rm',tool:null,category:null,purpose:null,meeting_id:null,outcome:'refused',basis:null,grant_id:null}]});
  assert.deepEqual(log.rows.map(r=>accessLine(r,'Sophie')),['Sophie saw your transaction insights to prepare for a meeting.',
    'A relationship manager who does not look after you tried to open your records. It was refused and recorded.']);
  assert.throws(()=>parseAccessLog({call_id:id(9),as_of:'2026-11-05T09:00:00+08:00',data:[{...log.rows[0],outcome:'copied'}]}));
  const asks=parseAsks({data:[{ask_id:id(20),category:'chat_topics',category_label:'Chat topics',reason:'meeting_prep',advisor_id:SOPHIE,rm_name:'Sophie Lim',
    meeting_id:id(7),meeting_starts_at:'2026-11-12T10:00:00+08:00',status:'open',asked_at:'2026-11-05T09:00:00+08:00',lapses_at:'2026-11-19T09:00:00+08:00',
    closed_at:null,reference:'TK-1234'},{ask_id:id(21),category:'transaction_insights',category_label:'Transaction insights',reason:'review',advisor_id:SOPHIE,rm_name:'Sophie Lim',
    meeting_id:null,meeting_starts_at:null,status:'lapsed',asked_at:'2026-10-01T09:00:00+08:00',lapses_at:'2026-10-15T09:00:00+08:00',closed_at:'2026-10-15T09:00:00+08:00',reference:null}]});
  assert.deepEqual(openAsks(asks).map(a=>a.ask_id),[id(20)]);
  assert.deepEqual(openAsks(asks,'transaction_insights'),[]);
  assert.equal(askLine(asks[0],day),'Sophie Lim asked to see your chat topics to prepare for your meeting on 2026-11-12.');
  assert.throws(()=>parseAsks({data:[{...asks[0],category:'meeting_recording'}]}));
  assert.deepEqual(DELETE_ALL,{category:'data_deletion',summary:'Please delete all my data.',urgency:'normal',details:{}});
});

test('a v2 grant and her Not now parse on the ledger with matching receipts only',()=>{
  const target={category:'chat_topics',recipient:SOPHIE,scope:{topic_areas:['investments']},notice_version:'ct-2026-11',until:'request',expires_on:null,meeting_id:null,
    purpose:'rm_brief',categories:['chat_topics'],category_label:'Chat topics',recipient_name:'Sophie Lim (DEMO)',replaces:null,expires_at:null};
  const stored=parseGrantV2Target({...target,after_receipt:null});
  assert.equal(stored.until,'request');
  assert.equal(parseGrantV2Result(stored,{...target,grant_id:id(44),status:'active',active_until:'2027-02-03T09:00:00+08:00',until_request_id:id(8)}).grant_id,id(44));
  assert.throws(()=>parseGrantV2Result(stored,{...target,scope:{topic_areas:['cards']},grant_id:id(44),status:'active'}));
  assert.equal(grantSentence(stored),'Shared chat topics with Sophie Lim (DEMO)');
  const ask=parseAskTarget({ask_id:id(20),category:'chat_topics',recipient:SOPHIE});
  assert.deepEqual(parseAskResult(ask,{...ask,status:'declined'}),{...ask,status:'declined'});
  assert.throws(()=>parseAskResult(ask,{...ask,ask_id:id(21),status:'declined'}));
  const command=(type,t,result,confirmed_at=null)=>({command:{command_id:id(30),case_id:id(31),customer_id:1002,type,target:t,idempotency_key:`${id(31)}:t1:${type}`,
    state:'succeeded',proposed_at:'2026-11-05T01:00:00Z',expires_at:'2026-11-05T01:30:00Z',confirmed_at},
    receipt:{receipt_id:`rcpt_${id(30).replaceAll('-','')}`,command_id:id(30),customer_id:1002,outcome:'succeeded',result,audit_event_id:7,created_at:'2026-11-05T01:01:00Z'}});
  const askTarget={ask_id:id(20),category:'chat_topics',recipient:SOPHIE};
  assert.equal(parseAction(command('consent.decline_ask',askTarget,{...askTarget,status:'declined'})).receipt.result.status,'declined');  // automatic
  const granted=command('consent.grant',{...target,after_receipt:null},{...target,grant_id:id(44),status:'active',active_until:null,until_request_id:id(8)},'2026-11-05T01:01:00Z');
  assert.equal(parseAction(granted).receipt.result.grant_id,id(44));
  assert.throws(()=>parseAction({...granted,command:{...granted.command,confirmed_at:null}}));  // shared only after her yes
});

test('offer switches are off unless she turns them on; a typed yes and Cancel send none',()=>{
  const offer=parseOffer({offer_group:id(1),expires_at:'2026-11-05T09:30:00Z',offers:[
    {command_id:id(2),type:'meeting.request',summary:'Send a meeting request to Sophie',expires_at:'2026-11-05T09:30:00Z',optional:false},
    {command_id:id(3),type:'consent.grant',summary:'Also share your chat topics with Sophie until this request is answered',expires_at:'2026-11-05T09:30:00Z',optional:true}]});
  assert.deepEqual([required(offer).length,switches(offer).length],[1,1]);
  assert.deepEqual(included(offer,{}),[]);
  assert.deepEqual(offerReply(offer,'confirm',{[id(3)]:true}),{offer_group:id(1),decision:'confirm',include:[id(3)]});
  assert.deepEqual(offerReply(offer,'confirm',{[id(2)]:true}),{offer_group:id(1),decision:'confirm'});  // only a switch can be turned on
  assert.deepEqual(offerReply(offer,'cancel',{[id(3)]:true}),{offer_group:id(1),decision:'cancel'});
  assert.throws(()=>parseOffer({...offer,offers:[{...offer.offers[1]}]}));  // a switch never leads
  assert.throws(()=>parseOffer({...offer,offers:[offer.offers[0],{...offer.offers[1],optional:'yes'}]}));
});
