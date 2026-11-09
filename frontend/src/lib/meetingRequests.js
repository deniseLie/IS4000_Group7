// Meeting requests (migration 0106; RM copilot build plan 2a). The client asks her relationship manager for a meeting with
// what it is about (her own words, redacted, at most 200 characters), 1-5 fixed topics, a format and 1-3 preferred times.
// Her RM accepts a time, suggests another (a held time she accepts or declines) or answers without a meeting. Nothing is
// booked from the form: every change is a confirm-type action (propose, then her own click confirms it).
import {redact} from './chatTools.js';
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const TOPICS={home_loan:'Property financing',investments:'Investments',fixed_deposit:'Time deposit',insurance:'Insurance',
  term_sheet:'Loan terms (term sheet)',compliance_review:'Periodic review',account_review:'Accounts and everyday banking',card_review:'Cards',other:'Something else'};
export const MEETING_FORMATS=['video','phone','in_person'];
export const MEETING_ACTIONS=['meeting.request','meeting.withdraw','meeting.counter_reply'];
export const STATUS={requested:'Waiting for your RM',countered:'New time suggested',scheduled:'Meeting booked',declined:'Answered without a meeting',withdrawn:'Withdrawn'};
export const DECLINED={answered_by_message:'Your relationship manager answered in a message instead.',
  redirected:'Your relationship manager pointed you to the right team.',not_needed:'Your relationship manager said a meeting isn’t needed for this.'};
export const LIMITS={purpose:200,topics:5,times:3,open:3};

function invalid() {return new Error('The meeting request could not be verified. Reload it before retrying.');}
function id(value) {if(typeof value!=='string'||!UUID.test(value))throw invalid();return value;}
function timestamp(value) {
  if(typeof value!=='string'||!/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(value)||!Number.isFinite(Date.parse(value)))throw invalid();
  return value;
}
function purpose(value) {if(typeof value!=='string'||!value.trim()||value.trim().length>LIMITS.purpose)throw invalid();return value;}
function topics(value) {
  if(!Array.isArray(value)||value.length<1||value.length>LIMITS.topics||value.some(topic=>!(topic in TOPICS))||new Set(value).size!==value.length)throw invalid();
  return [...value];
}
function times(value) {
  if(!Array.isArray(value)||value.length<1||value.length>LIMITS.times)throw invalid();
  return value.map(timestamp);
}
function format(value) {if(!MEETING_FORMATS.includes(value))throw invalid();return value;}

// The stored command target (SQL normalises topics and times; withdraw and counter replies add the RM's id).
export function parseMeetingTarget(type,value) {
  if(!value)throw invalid();
  if(type==='meeting.request')return {advisor_id:id(value.advisor_id),purpose:purpose(value.purpose),topics:topics(value.topics),format:format(value.format),preferred_times:times(value.preferred_times)};
  const ref={request_id:id(value.request_id),...(value.advisor_id===undefined?{}:{advisor_id:id(value.advisor_id)})};
  if(type==='meeting.withdraw')return ref;
  if(type!=='meeting.counter_reply'||!['accept','decline'].includes(value.reply))throw invalid();
  return {...ref,reply:value.reply};
}

// A succeeded receipt for that target: sent (with when her RM answers), withdrawn, or her answer to a proposed time.
export function parseMeetingResult(type,target,value) {
  if(!value)throw invalid();
  if(type==='meeting.request') {
    if(value.status!=='requested'||typeof value.respond_by_text!=='string'||!value.respond_by_text.trim())throw invalid();
    return {request_id:id(value.request_id),status:'requested',respond_by:timestamp(value.respond_by),respond_by_text:value.respond_by_text};
  }
  if(id(value.request_id)!==target.request_id)throw invalid();
  if(type==='meeting.withdraw') {
    if(value.status!=='withdrawn')throw invalid();
    return {request_id:target.request_id,status:'withdrawn'};
  }
  if(value.reply!==target.reply||value.status!==(target.reply==='accept'?'scheduled':'withdrawn'))throw invalid();
  if(target.reply==='decline')return {request_id:target.request_id,status:'withdrawn',reply:'decline'};
  return {request_id:target.request_id,status:'scheduled',reply:'accept',appointment_id:id(value.appointment_id),starts_at:timestamp(value.starts_at),
    when:typeof value.when==='string'?value.when:null};
}

// GET /relationship/meeting-requests: her own requests, newest first.
export function parseMeetingRequests(value) {
  if(!value||!Array.isArray(value.data)||value.data.length>50)throw invalid();
  const data=value.data.map(item=>{
    if(!item||!(item.status in STATUS))throw invalid();
    if(item.decline_reason!==null&&item.decline_reason!==undefined&&!(item.decline_reason in DECLINED))throw invalid();
    return {request_id:id(item.request_id),purpose:purpose(item.purpose),topics:topics(item.topics),format:format(item.format),status:item.status,
      respond_by:timestamp(item.respond_by),preferred_times:times(item.preferred_times),decline_reason:item.decline_reason??null,
      appointment_id:item.appointment_id===null||item.appointment_id===undefined?null:id(item.appointment_id),created_at:timestamp(item.created_at)};
  });
  if(new Set(data.map(item=>item.request_id)).size!==data.length)throw invalid();
  return {call_id:id(value.call_id),as_of:timestamp(value.as_of),data:data.sort((a,b)=>Date.parse(b.created_at)-Date.parse(a.created_at))};
}

export const isOpen=request=>['requested','countered'].includes(request.status);
export const openCount=requests=>(requests||[]).filter(isOpen).length;
// What she can do with one request: withdraw while it is open; accept or decline a time her RM suggested.
export function requestChoices(request) {
  if(request.status==='countered')return ['accept','decline','withdraw'];
  return request.status==='requested'?['withdraw']:[];
}
// The time her RM suggested: the held appointment named by a countered request.
export function suggestedTime(request,appointments) {
  if(request.status!=='countered'||!request.appointment_id)return null;
  return (appointments||[]).find(item=>item.appointment_id===request.appointment_id&&item.status==='held')||null;
}
// The appointments a request owns (a suggested or accepted time) are shown with the request, not as separate meetings.
export function requestAppointmentIds(requests) {return new Set((requests||[]).map(item=>item.appointment_id).filter(Boolean));}

// Form helpers: picking toggles a choice within its limit; the target is checked before anything is sent.
export function toggleTime(selected,startsAt) {
  if(selected.includes(startsAt))return selected.filter(item=>item!==startsAt);
  return selected.length>=LIMITS.times?selected:[...selected,startsAt].sort((a,b)=>Date.parse(a)-Date.parse(b));
}
export function toggleTopic(selected,topic) {
  if(!(topic in TOPICS))return selected;
  if(selected.includes(topic))return selected.filter(item=>item!==topic);
  return selected.length>=LIMITS.topics?selected:[...selected,topic];
}
export function meetingRequestTarget({advisorId,purpose:text,topics:chosen,format:kind,times:picked}) {
  const cleaned=redact(String(text||'')).replace(/\s+/g,' ').trim();
  if(!advisorId||!UUID.test(advisorId))throw new Error('You don’t have a relationship manager recorded right now.');
  if(!cleaned)throw new Error('Say briefly what you’d like to discuss.');
  if(cleaned.length>LIMITS.purpose)throw new Error(`Keep what it’s about to ${LIMITS.purpose} characters.`);
  if(!chosen?.length)throw new Error('Pick at least one topic.');
  if(chosen.length>LIMITS.topics||chosen.some(topic=>!(topic in TOPICS)))throw new Error(`Pick up to ${LIMITS.topics} topics from the list.`);
  if(!MEETING_FORMATS.includes(kind))throw new Error('Pick how you’d like to meet.');
  if(!picked?.length)throw new Error('Pick at least one time that suits you.');
  if(picked.length>LIMITS.times)throw new Error(`Pick up to ${LIMITS.times} times.`);
  return {advisor_id:advisorId,purpose:cleaned,topics:[...new Set(chosen)].sort(),format:kind,
    preferred_times:[...new Set(picked)].sort((a,b)=>Date.parse(a)-Date.parse(b))};
}

// The chat's meeting request draft (services/agents/meeting_answers.py writes it in a fixed form) read back into fields
// for the inline card: who, what it's about, topics, format and the times as labels ("Mon 16 Nov, 9:00 am").
// Returns null when the text is not that form, so the reply is shown as plain text with its Confirm and Cancel.
export function parseMeetingDraft(text) {
  const value=String(text||'');
  const rm=value.match(/send your relationship manager, (.+?), this meeting request:/);
  const about=value.match(/What it[’']s about: “([^”]*)”/),topics=value.match(/• Topics: ([^\n]+)/),format=value.match(/• Format: ([^\n]+)/);
  const times=value.match(/• Times that suit you \(Singapore time\): ([^\n]+)/);
  if(!rm||!about||!topics||!format||!times)return null;
  const byLabel=Object.fromEntries(Object.entries(TOPICS).map(([code,label])=>[label.toLowerCase(),code]));
  const formatCode=Object.entries({video:'video call',phone:'phone call',in_person:'in person'}).find(([,label])=>label===format[1].trim().toLowerCase())?.[0]||'video';
  return {rm:rm[1].trim(),note:value.slice(0,rm.index).replace(/I can\s*$/,'').trim(),purpose:aboutOnly(about[1]),
    topics:topics[1].split(',').map(label=>byLabel[label.trim().toLowerCase()]).filter(Boolean),format:formatCode,
    times:times[1].split(';').map(label=>label.trim()).filter(Boolean)};
}

// "What it's about" without the ask itself: "I'm thinking of buying a home. Can I meet my RM?" -> "I'm thinking of buying a home."
// When the ask is all she wrote, it is kept.
export function aboutOnly(text) {
  const value=String(text||'').trim();
  const kept=value.replace(/\s*(?:can|could|may)\s+i\s+(?:please\s+)?(?:meet|see|speak (?:to|with)|talk (?:to|with)|book (?:a )?(?:meeting|call|time) with)\s+(?:my|the|your)\s+(?:relationship manager|rm|banker|adviser|advisor)[^.?!]*[?.!]?\s*$/i,'').trim();
  return kept||value;
}

// The same label the chat uses for a free time (Singapore time), so a drafted time can be matched to a free slot.
export function slotLabel(value) {
  const parts=Object.fromEntries(new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Singapore',weekday:'short',day:'numeric',month:'short',hour:'numeric',minute:'2-digit',hour12:true})
    .formatToParts(new Date(value)).map(p=>[p.type,p.value]));
  return `${parts.weekday} ${parts.day} ${parts.month}, ${parts.hour}:${parts.minute} ${String(parts.dayPeriod).toLowerCase()}`;
}
