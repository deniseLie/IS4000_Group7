// Workflow 3 (David): in-chat relationship steps. Pure helpers are unit-tested; every change still goes through the Action Service.
import {confirmAction,createActionCase,proposeAction,rejectAction} from './actions.js';
import {loadConsentRecords} from './consent.js';
import {formatDate} from './format.js';
const API_URL=import.meta.env?.VITE_API_URL || '/api';
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DAY=/^\d{4}-\d\d-\d\d$/;
const ANCHORS=['team-restructure','continuity','unrecorded','no-advisor'];
export const FORMATS={video:'Video call',phone:'Phone call',in_person:'In person'};
export const REQUEST_STEPS=['sent','seen','answered'];

function invalid() {return new Error('The relationship records could not be verified. Please retry.');}
function id(value) {if(typeof value!=='string'||!UUID.test(value))throw invalid();return value;}
function text(value,max=2000) {if(typeof value!=='string'||!value.trim()||value.length>max)throw invalid();return value;}
function day(value) {if(typeof value!=='string'||!DAY.test(value)||!Number.isFinite(Date.parse(value)))throw invalid();return value;}
function timestamp(value) {
  if(typeof value!=='string'||!/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(value)||!Number.isFinite(Date.parse(value)))throw invalid();
  return value;
}

// Which in-chat step a customer message points to. Order matters: a meeting request mentioning "request" is still a meeting.
const INTENTS=[
  ['note',/\bhandover(?:\s+note)?\b|\bprepare (?:a |my )?note\b|\bagenda\b/i],
  ['meeting',/\b(?:meet|meeting|appointment|book(?:ing)?|schedule|catch up|call with)\b/i],
  ['status',/\b(?:what'?s|whats|what is)\s+happening\s+with\s+my\s+requests?\b|\bstatus\s+of\s+my\s+requests?\b|\bmy\s+(?:open\s+|pending\s+)?requests?\b|\bupdate\s+on\s+my\s+requests?\b/i],
  ['request',/\b(?:can|could|would)\s+(?:someone|somebody|you|my\s+(?:rm|relationship manager|advisor|adviser|banker))\s+(?:please\s+)?(?:send|arrange|prepare|get|call|email|post)\b|\bplease\s+(?:send|arrange|prepare)\b|\bi\s+(?:need|want)\s+(?:a|an|my)\b.*\b(?:statement|letter|form|report|copy)\b/i],
  ['change',/\b(?:why|how come)\b.*\b(?:leave|left|change[ds]?|go(?:ne)?|moved?|replaced?)\b|\bstay the same\b|\b(?:old|previous|former)\s+(?:guy|lady|person|rm|banker|manager|advisor|adviser)\b/i],
  ['intro',/\bwho\s+is\s+my\s+(?:new\s+)?(?:rm|relationship manager|advisor|adviser|banker)\b/i],
];
export function relationshipIntent(message) {
  if(typeof message!=='string')return null;
  return INTENTS.find(([,pattern])=>pattern.test(message))?.[0]||null;
}

// Turn "estate planning, review of fees, how often we meet" into short agenda items, keeping the customer's words.
export function organiseAgenda(input,limit=12) {
  if(typeof input!=='string')return [];
  const seen=new Set(),items=[];
  for(const raw of input.split(/\r?\n|;|,|\s+and\s+(?=\w)|•|\u2022|^\s*-\s+/gm)) {
    let item=raw.replace(/^\s*(?:and|also|plus|\d+[.)]|-|\*)\s+/i,'').replace(/[\s.;:!?]+$/,'').replace(/\s+/g,' ').trim();
    if(!item)continue;
    item=item[0].toUpperCase()+item.slice(1);
    if(Array.from(item).length>280)item=Array.from(item).slice(0,279).join('')+'…';
    const key=item.toLowerCase();
    if(seen.has(key))continue;
    seen.add(key);items.push(item);
    if(items.length>=limit)break;
  }
  return items;
}

// Note lines are saved as agenda topics: drafted lines first, then numbered agenda items.
export function noteLines(drafted,agenda) {
  const kept=drafted.filter(line=>!/^Agenda item \d+:/.test(line));
  return [...kept,...agenda.map((item,index)=>`Agenda item ${index+1}: ${item}`)].slice(0,12);
}
export function splitNote(lines) {
  const agenda=[],drafted=[];
  for(const line of lines) {const match=line.match(/^Agenda item \d+:\s*(.+)$/);if(match)agenda.push(match[1]);else drafted.push(line);}
  return {drafted,agenda};
}

function singapore(now) {return new Date(now.getTime()+8*60*60*1000);}
function iso(date) {return date.toISOString().slice(0,10);}
function plus(date,days) {return new Date(date.getTime()+days*86400000);}
// Dates the customer asked about, as Singapore calendar days within the 14-day search window the API allows.
export function meetingDays(message,now=new Date()) {
  const today=singapore(now);today.setUTCHours(0,0,0,0);
  const weekday=today.getUTCDay();const text=message||'';
  let from=today,to=plus(today,6);
  if(/\bnext\s+week\b/i.test(text)) {from=plus(today,((8-weekday)%7)||7);to=plus(from,4);}
  else if(/\bthis\s+week\b/i.test(text)) {to=plus(today,(7-weekday)%7);}
  else if(/\btomorrow\b/i.test(text)) {from=plus(today,1);to=from;}
  else if(/\btoday\b/i.test(text)) {to=today;}
  const days=[];
  for(let cursor=from;cursor<=to&&days.length<7;cursor=plus(cursor,1)) {if(cursor.getUTCDay()!==0&&cursor.getUTCDay()!==6)days.push(iso(cursor));}
  return days;
}

export function requestProgress(status) {
  if(status==='closed')return {done:REQUEST_STEPS.length,label:'Closed'};
  const index=REQUEST_STEPS.indexOf(status);
  return {done:index+1,label:status[0].toUpperCase()+status.slice(1)};
}
export {formatDate as formatDay};

function advisor(value,previous=false) {
  if(previous)return {name:text(value.name),role:text(value.role),team:text(value.team),started_on:day(value.started_on),ended_on:day(value.ended_on)};
  const contact=value.contact||{};
  return {rm_id:id(value.rm_id),name:text(value.name),role:text(value.role),team:text(value.team),started_on:day(value.started_on),
    contact:{email:contact.email===null?null:text(contact.email),phone:contact.phone===null?null:text(contact.phone)}};
}
export function parseIntroduction(value) {
  const data=value?.data;
  if(!data||!Array.isArray(data.notice)||data.notice.length>2)throw invalid();
  const current=data.current===null?null:advisor(data.current),previous=data.previous===null?null:advisor(data.previous,true);
  const notice=data.notice.map(item=>{
    if(!ANCHORS.includes(item?.anchor)||item.article_id!=='rm-change'||!Number.isSafeInteger(item.version)||item.version<1)throw invalid();
    return {anchor:item.anchor,heading:text(item.heading),body:text(item.body),title:text(item.title),publisher:text(item.publisher),
      url:`/knowledge/sections/${item.article_id}/${item.version}/${item.anchor}`,version:item.version};
  });
  const anchors=notice.map(item=>item.anchor).join(',');
  const expected=current===null?anchors==='no-advisor':previous===null?anchors==='':/^(team-restructure|unrecorded),continuity$/.test(anchors);
  if(!expected)throw invalid();
  return {call_id:id(value.call_id),as_of:timestamp(value.as_of),current,previous,notice};
}
// A recent recorded change (within 60 days) opens the introduction automatically when the chat starts.
export function recentChange(intro,now=new Date(),days=60) {
  if(!intro?.current||!intro.previous)return false;
  const started=Date.parse(`${intro.current.started_on}T00:00:00+08:00`);
  return now.getTime()-started<=days*86400000&&now.getTime()>=started-86400000;
}
export function parseMeetingOptions(value) {
  if(!value||!Array.isArray(value.data)||value.data.length>3)throw invalid();
  const options={};
  for(const item of value.data) {
    if(!Object.hasOwn(FORMATS,item?.format)||options[item.format])throw invalid();
    options[item.format]={advisor_id:id(item.advisor_id),joining_details:text(item.joining_details,500)};
  }
  return {call_id:id(value.call_id),as_of:timestamp(value.as_of),options};
}
export function parseHandoverDraft(value) {
  if(!value||!Array.isArray(value.topics)||!value.topics.length||value.topics.length>8||!Array.isArray(value.profile_fields)||value.profile_fields.length>3||
    value.profile_fields.some(field=>!['name','preferred_language','employment_status'].includes(field))||!Array.isArray(value.sources)||!value.sources.length)throw invalid();
  return {advisor_id:id(value.advisor_id),recipient_name:text(value.recipient_name,200),profile_fields:[...value.profile_fields],
    topics:value.topics.map(topic=>text(topic,300)),sources:value.sources.map(item=>({tool:text(item.tool,80),call_id:id(item.call_id),as_of:timestamp(item.as_of)}))};
}

async function body(response) {
  let value;
  try {value=await response.json();}
  catch(error) {if(error instanceof SyntaxError)throw new Error('The relationship response could not be verified. Please retry.',{cause:error});throw error;}
  if(!response.ok)throw new Error(typeof value?.error?.message==='string'&&value.error.message.trim()?value.error.message:'Relationship records cannot be loaded. Please retry.');
  return value;
}
const get=(path,token,signal)=>fetch(`${API_URL}${path}`,{headers:{Authorization:`Bearer ${token}`},signal}).then(body);
export async function loadIntroduction(token,signal) {return parseIntroduction(await get('/relationship/introduction',token,signal));}
export async function loadMeetingOptions(token,signal) {return parseMeetingOptions(await get('/relationship/meeting-options',token,signal));}
export async function loadHandoverDraft(token,signal) {return parseHandoverDraft(await get('/relationship/handover-draft',token,signal));}

async function caseFor(token,signal) {return createActionCase({session_id:crypto.randomUUID(),case_id:crypto.randomUUID()},token,signal);}
export async function proposeIn(type,target,token,signal) {
  const scope=await caseFor(token,signal);
  return proposeAction({command_id:crypto.randomUUID(),case_id:scope.case_id,task_id:crypto.randomUUID(),type,target},token,signal);
}
export async function decide(commandId,confirmed,token,signal) {return (confirmed?confirmAction:rejectAction)(commandId,token,signal);}

export function coveringGrant(registry,advisorId,categories,now=Date.now()) {
  return (registry?.grants||[]).find(grant=>grant.active&&grant.revoked_at===null&&grant.purpose==='rm_brief'&&grant.recipient===advisorId&&
    Date.parse(grant.granted_at)<=now&&(grant.expires_at===null||Date.parse(grant.expires_at)>now)&&categories.every(category=>grant.categories.includes(category)))||null;
}

// One explicit consent in chat: reuse a covering grant or record request → grant, then share. Each step keeps its own receipt;
// onStep reports progress so a partial failure is shown exactly (e.g. consent recorded but not yet shared).
export async function shareWithConsent({agenda,durationDays,token,signal,onStep=()=>{}}) {
  const receipts=[];
  let grant=coveringGrant(await loadConsentRecords(token,signal),agenda.advisor_id,agenda.categories);
  if(!grant) {
    onStep('consent_request');
    const requested=await proposeIn('consent.request',{purpose:'rm_brief',recipient:agenda.advisor_id,categories:agenda.categories,duration_days:durationDays},token,signal);
    if(requested.receipt?.outcome!=='succeeded')throw new Error('Your consent could not be recorded because the recipient changed. Nothing was shared. Reload and try again.');
    receipts.push(requested.receipt.receipt_id);
    onStep('consent_grant');
    const proposed=await proposeIn('consent.grant',{request_id:requested.receipt.result.request_id},token,signal);
    const granted=await confirmAction(proposed.command.command_id,token,signal);
    if(granted.receipt?.outcome!=='succeeded')throw new Error('Your consent was not granted because its scope changed. Nothing was shared.');
    receipts.push(granted.receipt.receipt_id);
    grant={grant_id:granted.receipt.result.grant_id,expires_at:granted.receipt.result.expires_at};
  }
  onStep('share');
  const proposal=await proposeIn('rm.share_summary',{agenda_id:agenda.agenda_id,agenda_revision:agenda.revision,advisor_id:agenda.advisor_id,consent_grant_id:grant.grant_id},token,signal);
  const shared=await confirmAction(proposal.command.command_id,token,signal);
  if(shared.receipt?.outcome!=='succeeded')throw new Error('Consent is recorded, but the note was not shared because the note, consent or recipient changed. Reload and try again.');
  receipts.push(shared.receipt.receipt_id);
  return {shared,grant,receipts};
}
