// Talking points (0108, build plan phase 4): Data sharing → What <RM> sees. Each topic shows the exact line her
// relationship manager reads (list values only); her own words under "Why we think this" never leave her side.
// Delete, Don't suggest this again and Resume are automatic actions with a receipt; Let her know is a chat offer.
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DAY=/^\d{4}-\d\d-\d\d$/;
const API_URL=import.meta.env?.VITE_API_URL || '/api';
export const TOPICS={home_loan:'Home loan',investments:'Investments',fixed_deposit:'Fixed deposit',insurance:'Insurance'};
export const STAGES=['curious','exploring','planning','ready','concern'];
export const TIMEFRAMES={now:'now',within_3m:'within 3 months',within_12m:'within 12 months',later:'later'};
export const CONCERNS=['rates','fees','eligibility','timing'];
export const ORIGINS={inferred:'inferred from chat',confirmed:'shared by the client'};
export const INSIGHT_ACTIONS=['insight.delete','insight.mute','insight.resume','insight.share','insight.restore'];  // restore: 0110 Undo
export const REMOVED_REASONS={client:'You deleted it',muted:'You asked not to suggest it',chat_topics_revoked:'You stopped sharing chat topics',
  scope_narrowed:'Outside the topic areas you share'};

function invalid() {return new Error('Your talking points could not be verified. Refresh to try again.');}
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

// The one rendering (schemas/meetings.TalkingPoint.line): 'Home loan · planning · within 12 months · inferred from chat'.
export function line(point) {
  return [TOPICS[point.topic],point.stage,...(point.timeframe&&point.timeframe!=='unknown'?[TIMEFRAMES[point.timeframe]]:[]),
    ...(point.concerns?.length?[`asks about ${point.concerns.join(', ')}`]:[]),ORIGINS[point.origin]].join(' · ');
}

function parsePoint(value) {
  if(!value||!Object.hasOwn(ORIGINS,value.origin)||typeof value.pending!=='boolean'||!Array.isArray(value.evidence)||value.evidence.length>3)throw invalid();
  const point={point_id:id(value.point_id),topic:topic(value.topic),stage:stage(value.stage),timeframe:timeframe(value.timeframe),
    concerns:concerns(value.concerns),origin:value.origin,first_seen:day(value.first_seen),last_seen:day(value.last_seen),
    visible_from:timestamp(value.visible_from),pending:value.pending,
    evidence:value.evidence.map(q=>{if(typeof q?.quote!=='string'||!q.quote.trim()||q.quote.length>200)throw invalid();return {quote:q.quote,recorded_at:timestamp(q.recorded_at)};})};
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
  if(!response.ok)throw new Error(typeof body?.error?.message==='string'&&body.error.message.trim()?body.error.message:'Your talking points could not be loaded. Refresh to try again.');
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

export function chatTopicsNote(rm) {return `Topics from this chat may be shared with ${rm||'your relationship manager'} for your meetings.`;}
export function pauseNote(until,format) {return `Topic suggestions are paused until ${format(until)}.`;}
