// Client chat insights (talking points in the code; 0108, build plan phase 4): Data sharing → Chat insights. Each reads in plain words with
// the same facts as the line her relationship manager reads (list values only). Her words are never shown or kept (0142).
// Delete, Don't suggest this again and Allow again are automatic actions with a receipt; Let her know is a chat offer. Since 0143 a
// point may name a kind (KINDS, = schemas/meetings.KINDS) and be on hold (she said the plan is off: her RM does not see it).
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DAY=/^\d{4}-\d\d-\d\d$/;
const API_URL=import.meta.env?.VITE_API_URL || '/api';
export const TOPICS={home_loan:'Property financing',investments:'Investments',fixed_deposit:'Time deposit',insurance:'Insurance'};
export const STAGES=['curious','exploring','planning','ready','concern'];
export const TIMEFRAMES={now:'now',within_3m:'within 3 months',within_12m:'within 12 months',later:'later'};
export const CONCERNS=['rates','fees','eligibility','timing','lock_in','down_payment','tenure','risk'];  // = schemas/meetings.ConcernTag
const CONCERN_TEXT={lock_in:'lock-in',down_payment:'down payment'};  // = schemas/meetings.CONCERN_TEXT (the rest read as their code)
export const KINDS={home_loan:['hdb_bto','hdb_resale','private_property','landed','refinance'],investments:['etf','unit_trust','stocks','bonds','robo'],
  fixed_deposit:['new_deposit','renewal'],insurance:['life','home','travel']};
export const KIND_TEXT={hdb_bto:'new launch property',hdb_resale:'resale property',private_property:'private property',landed:'landed property',refinance:'refinancing',etf:'ETFs',
  unit_trust:'unit trusts',stocks:'stocks',bonds:'bonds',robo:'managed portfolio',new_deposit:'new deposit',renewal:'renewal',life:'life',home:'home',travel:'travel'};
export const ORIGINS={inferred:'inferred from chat',confirmed:'shared by the client'};
export const INSIGHT_ACTIONS=['insight.delete','insight.mute','insight.resume','insight.share','insight.restore'];  // restore: 0110 Undo
export const REMOVED_REASONS={client:'You deleted it',muted:'You asked not to suggest it',chat_topics_revoked:'You stopped sharing chat topics',
  scope_narrowed:'Outside the topic areas you share'};

function invalid() {return new Error('Your chat insights could not be verified. Refresh to try again.');}
function id(value) {if(typeof value!=='string'||!UUID.test(value))throw invalid();return value;}
function timestamp(value) {
  if(typeof value!=='string'||!/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(value)||!Number.isFinite(Date.parse(value)))throw invalid();
  return value;
}
function day(value) {if(typeof value!=='string'||!DAY.test(value))throw invalid();return value;}
function topic(value) {if(!Object.hasOwn(TOPICS,value))throw invalid();return value;}
function stage(value) {if(!STAGES.includes(value))throw invalid();return value;}
function timeframe(value) {if(value!=='unknown'&&!Object.hasOwn(TIMEFRAMES,value))throw invalid();return value;}
function concerns(value) {
  if(!Array.isArray(value)||value.length>4||value.some(c=>!CONCERNS.includes(c))||new Set(value).size!==value.length)throw invalid();
  return [...value];
}
function kind(code,value) {if(value===null||value===undefined)return null;if(!KINDS[code]?.includes(value))throw invalid();return value;}
const concernText=c=>CONCERN_TEXT[c]||c;

// The one rendering (schemas/meetings.TalkingPoint.line): 'Property financing · planning · within 12 months · inferred from chat',
// with the kind after the stage when she named one ('Property financing · planning · resale property · within 12 months · …').
export function line(point) {
  return [TOPICS[point.topic],point.stage,...(point.kind?[KIND_TEXT[point.kind]]:[]),...(point.timeframe&&point.timeframe!=='unknown'?[TIMEFRAMES[point.timeframe]]:[]),
    ...(point.concerns?.length?[`asks about ${point.concerns.map(concernText).join(', ')}`]:[]),ORIGINS[point.origin]].join(' · ');
}

// Her view in plain words: the same facts as line(), without the list codes, in the labels since 0117 (Property financing,
// Time deposit). 'You're planning to finance a property (resale property) in the next 12 months.'
const ABOUT={home_loan:'property financing',investments:'investing',fixed_deposit:'time deposits',insurance:'insurance'};
const PLAN={home_loan:'to finance a property',investments:'to invest',fixed_deposit:'a time deposit',insurance:'to get insurance cover'};
const ACT={home_loan:'apply for property financing',investments:'start investing',fixed_deposit:'place a time deposit',insurance:'take up insurance cover'};
const OWN={home_loan:'your property financing',investments:'your investments',fixed_deposit:'your time deposit',insurance:'your insurance'};
const WHEN={now:'now',within_3m:'in the next 3 months',within_12m:'in the next 12 months',later:'later on'};
const IN_MIND={now:', for right now',within_3m:', with the next 3 months in mind',within_12m:', with the next 12 months in mind',later:', with later on in mind'};
function words(list) {return list.length<2?list.join(''):`${list.slice(0,-1).join(', ')} and ${list.at(-1)}`;}
export function plainLine(point) {
  const t=point.topic,known=point.timeframe&&point.timeframe!=='unknown';
  const acting=point.stage==='planning'||point.stage==='ready';
  const base={curious:`You asked about ${ABOUT[t]}`,exploring:`You're looking into ${ABOUT[t]}`,planning:`You're planning ${PLAN[t]}`,
    ready:`You're ready to ${ACT[t]}`,concern:`You raised a concern about ${OWN[t]}`}[point.stage];
  const when=known?(acting?` ${WHEN[point.timeframe]}`:IN_MIND[point.timeframe]):'',which=point.kind?` (${KIND_TEXT[point.kind]})`:'';
  return `${base}${which}${when}.${point.concerns?.length?` You mentioned ${words(point.concerns.map(concernText))}.`:''}`;
}

function parsePoint(value) {
  if(!value||!Object.hasOwn(ORIGINS,value.origin)||typeof value.pending!=='boolean')throw invalid();
  if(value.on_hold!==undefined&&typeof value.on_hold!=='boolean')throw invalid();  // 0143; a server before it sends none
  const point={point_id:id(value.point_id),topic:topic(value.topic),stage:stage(value.stage),kind:kind(value.topic,value.kind),
    timeframe:timeframe(value.timeframe),concerns:concerns(value.concerns),origin:value.origin,first_seen:day(value.first_seen),
    last_seen:day(value.last_seen),visible_from:timestamp(value.visible_from),pending:value.pending,on_hold:value.on_hold===true,
    on_hold_at:value.on_hold_at===null||value.on_hold_at===undefined?null:timestamp(value.on_hold_at)};
  if(value.line!==line(point))throw invalid();  // what she sees is exactly what her relationship manager reads
  return {...point,line:value.line};
}

// 0110: topics removed in the last 7 days; one she deleted herself can be restored until restore_until.
function parseRemoved(value) {
  if(!value||!Object.hasOwn(REMOVED_REASONS,value.reason)||typeof value.can_restore!=='boolean')throw invalid();
  return {point_id:id(value.point_id),topic:topic(value.topic),reason:value.reason,removed_at:timestamp(value.removed_at),
    restore_until:value.restore_until===null||value.restore_until===undefined?null:timestamp(value.restore_until),can_restore:value.can_restore};
}

export function parseTalkingPoints(value) {
  const data=value?.data;
  if(!data||!Array.isArray(data.points)||data.points.length>4||!Array.isArray(data.muted)||typeof data.chat_topics!=='boolean')throw invalid();
  if(data.removed!==undefined&&(!Array.isArray(data.removed)||data.removed.length>20))throw invalid();
  const rm=data.rm_first_name===null||data.rm_first_name===undefined?null:data.rm_first_name;
  if(rm!==null&&(typeof rm!=='string'||!rm.trim()||rm.length>80))throw invalid();
  const pause=data.pause===null||data.pause===undefined?null:{pause_id:id(data.pause.pause_id),until:timestamp(data.pause.until)};
  const points=data.points.map(parsePoint);
  if(new Set(points.map(p=>p.point_id)).size!==points.length)throw invalid();
  return {points,muted:data.muted.map(topic),pause,chat_topics:data.chat_topics,rm_first_name:rm,removed:(data.removed||[]).map(parseRemoved),as_of:timestamp(value.as_of)};
}

export async function loadTalkingPoints(token,signal) {
  const response=await fetch(`${API_URL}/customer/talking-points`,{headers:{Authorization:`Bearer ${token}`},signal});
  let body;
  try {body=await response.json();}
  catch(error) {if(error instanceof SyntaxError)throw invalid();throw error;}
  if(!response.ok)throw new Error(typeof body?.error?.message==='string'&&body.error.message.trim()?body.error.message:'Your chat insights could not be loaded. Refresh to try again.');
  return parseTalkingPoints(body);
}

// Ledger targets and receipts (services/actions/insights.py); the stored target also names the topic or the RM.
export function parseInsightTarget(type,value) {
  if(!value)throw invalid();
  if(type==='insight.delete'||type==='insight.restore')return {point_id:id(value.point_id),...(value.topic===undefined?{}:{topic:topic(value.topic)})};
  if(type==='insight.mute')return {topic:topic(value.topic)};
  if(type==='insight.resume')return {topic:value.topic===null||value.topic===undefined?null:topic(value.topic)};
  if(type!=='insight.share')throw invalid();
  return {topic:topic(value.topic),stage:stage(value.stage),timeframe:timeframe(value.timeframe??'unknown'),
    ...(value.advisor_id===undefined?{}:{advisor_id:id(value.advisor_id)})};
}
export function parseInsightResult(type,target,value) {
  if(!value)throw invalid();
  if(type==='insight.delete') {
    if(value.status!=='deleted'||id(value.point_id)!==target.point_id)throw invalid();
    return {point_id:target.point_id,topic:topic(value.topic),status:'deleted',
      restore_until:value.restore_until===null||value.restore_until===undefined?null:timestamp(value.restore_until)};
  }
  if(type==='insight.restore') {
    if(value.status!=='restored'||id(value.point_id)!==target.point_id)throw invalid();
    return {point_id:target.point_id,topic:topic(value.topic),status:'restored'};
  }
  if(type==='insight.mute') {
    if(value.status!=='muted'||value.topic!==target.topic||!Number.isSafeInteger(value.removed)||value.removed<0)throw invalid();
    return {topic:target.topic,status:'muted',removed:value.removed};
  }
  if(type==='insight.resume') {
    if(value.status!=='resumed'||(value.topic??null)!==target.topic)throw invalid();
    return {topic:target.topic,status:'resumed'};
  }
  if(value.origin!=='confirmed'||value.topic!==target.topic||typeof value.notified!=='boolean')throw invalid();
  return {point_id:id(value.point_id),topic:target.topic,stage:stage(value.stage),timeframe:timeframe(value.timeframe),origin:'confirmed',notified:value.notified};
}

// Actions page wording: one sentence per recorded change.
export function insightSentence(type,target) {
  const name=target.topic?TOPICS[target.topic]:null;
  if(type==='insight.delete')return name?`Deleted the ${name.toLowerCase()} topic from what your relationship manager sees`:'Deleted a topic from what your relationship manager sees';
  if(type==='insight.restore')return name?`Restored the ${name.toLowerCase()} topic you deleted`:'Restored a topic you deleted';
  if(type==='insight.mute')return `Stopped suggesting ${name.toLowerCase()} as a topic`;
  if(type==='insight.resume')return name?`Suggest ${name.toLowerCase()} as a topic again`:'Resumed topic suggestions';
  return `Let your relationship manager know: ${line({...target,concerns:[],origin:'confirmed'})}`;
}

// RM Today (GET /rm/shared-topics): topics clients shared with her themselves, newest first; a row with a notice is new.
// Each line is checked against the rendering; a row that does not match is left out, never shown.
export function sharedTopicRows(rows,now=Date.now()) {
  return (Array.isArray(rows)?rows:[]).flatMap(row=>{
    const point=row?.point;
    if(!point||point.origin!=='confirmed'||typeof row.client!=='string'||!Number.isSafeInteger(row.customer_id)||!Object.hasOwn(TOPICS,point.topic)||row.line!==line(point))return [];
    return [{share_id:row.share_id,customer_id:row.customer_id,client:row.client,topic:point.topic,line:row.line,shared_text:row.shared_text||'',
      notice:typeof row.notice==='string'?row.notice:null,isNew:!!row.notice&&now-Date.parse(row.shared_at)<86400000}];
  }).slice(0,20);
}

export function chatTopicsNote(rm) {return `Insights from this chat may be shared with ${rm||'your relationship manager'} for your meetings.`;}
