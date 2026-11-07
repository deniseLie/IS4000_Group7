// Consent v2 (0109-0113, build plan phase 6): Data sharing as one card per category with a scope and an end, the
// always-shared list, her RM's asks, who looked at what (the access log), and the ledger targets each switch sends.
// Every response is checked before it is shown; a row that does not fit is an error, never a guess.
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DAY=/^\d{4}-\d\d-\d\d$/;
const CODE=/^[a-z][a-z0-9_.]{0,60}$/;
const API_URL=import.meta.env?.VITE_API_URL || '/api';
export const GATED=['transaction_insights','chat_topics','meeting_recording'];
export const SWITCHABLE=['transaction_insights','chat_topics'];
export const TOPIC_AREAS={home_property:'Home and property',investments:'Investments',deposits:'Deposits',insurance:'Insurance',cards:'Cards',
  everyday_banking:'Everyday banking'};
export const LOOKBACK=[3,6,12];
export const UNTIL={date:'Until a date',meeting:'Until my next meeting ends',request:'Until my meeting request is answered'};
export const ASK_REASONS={meeting_prep:'to prepare for your meeting',review:'for your relationship review',advice:'to suggest options that fit your plans'};

function invalid() {return new Error('Your data-sharing settings could not be verified. Refresh to try again.');}
function id(value) {if(typeof value!=='string'||!UUID.test(value))throw invalid();return value;}
function maybeId(value) {return value===null||value===undefined?null:id(value);}
function timestamp(value) {
  if(typeof value!=='string'||!/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(value)||!Number.isFinite(Date.parse(value)))throw invalid();
  return value;
}
function maybeTime(value) {return value===null||value===undefined?null:timestamp(value);}
function day(value) {if(typeof value!=='string'||!DAY.test(value))throw invalid();return value;}
function text(value,max=400) {if(typeof value!=='string'||!value.trim()||value.length>max)throw invalid();return value;}
function maybeText(value,max=400) {return value===null||value===undefined?null:text(value,max);}
function code(value) {if(typeof value!=='string'||!CODE.test(value))throw invalid();return value;}
function maybeCode(value) {return value===null||value===undefined?null:code(value);}
function notice(value) {if(typeof value!=='string'||!/^[a-z0-9.-]{1,20}$/.test(value))throw invalid();return value;}  // consent.categories (0109)
function maybeNotice(value) {return value===null||value===undefined?null:notice(value);}

// The scope she chose (consent.normal_scope): accounts 'all' or her account ids, 3/6/12 months; or her topic areas.
export function parseScope(category,value) {
  if(category==='meeting_recording')return {};
  if(!value||typeof value!=='object')throw invalid();
  if(category==='transaction_insights') {
    const accounts=value.accounts==='all'?'all':Array.isArray(value.accounts)&&value.accounts.length&&value.accounts.length<=20&&value.accounts.every(Number.isSafeInteger)?[...value.accounts]:null;
    if(accounts===null||!LOOKBACK.includes(value.lookback_months))throw invalid();
    return {accounts,lookback_months:value.lookback_months};
  }
  if(category!=='chat_topics'||!Array.isArray(value.topic_areas)||!value.topic_areas.length||value.topic_areas.some(a=>!Object.hasOwn(TOPIC_AREAS,a)))throw invalid();
  return {topic_areas:[...value.topic_areas]};
}

function parseGrant(category,value) {
  if(value===null||value===undefined)return null;
  if(typeof value.legacy!=='boolean'||!['consent','notice'].includes(value.basis)||typeof value.renewal_due!=='boolean')throw invalid();
  if(value.until!==null&&!Object.hasOwn(UNTIL,value.until))throw invalid();
  return {grant_id:id(value.grant_id),legacy:value.legacy,scope:value.legacy||value.scope===null?null:parseScope(category,value.scope),until:value.until,basis:value.basis,
    granted_at:timestamp(value.granted_at),expires_at:maybeTime(value.expires_at),until_meeting_id:maybeId(value.until_meeting_id),
    until_request_id:maybeId(value.until_request_id),notice_version:maybeNotice(value.notice_version),renewal_due:value.renewal_due};
}

export function parseSharing(value) {
  const data=value?.data;
  if(!data||!Array.isArray(data.categories)||!data.categories.length||data.categories.length>12||!Array.isArray(data.accounts)||data.accounts.length>20)throw invalid();
  const rm=data.rm===null||data.rm===undefined?null:{advisor_id:id(data.rm.advisor_id),name:text(data.rm.name,200)};
  const categories=data.categories.map(c=>{
    if(typeof c?.gated!=='boolean'||typeof c.default_on!=='boolean'||typeof c.withdrawable!=='boolean')throw invalid();
    if(c.withdrawable&&(!Number.isSafeInteger(c.max_months)||c.max_months<1||c.max_months>12))throw invalid();
    return {code:code(c.code),label:text(c.label,80),description:text(c.description,600),gated:c.gated,default_on:c.default_on,withdrawable:c.withdrawable,
      max_months:c.max_months??null,notice_version:maybeNotice(c.notice_version),reason:maybeText(c.reason,300),grant:c.gated?parseGrant(c.code,c.grant):null};
  });
  const accounts=data.accounts.map(a=>{
    if(!Number.isSafeInteger(a?.account_id))throw invalid();
    return {account_id:a.account_id,type:text(a.type,60),number:text(a.number,40),currency:text(a.currency,3)};
  });
  const next=data.next_meeting===null||data.next_meeting===undefined?null:{meeting_id:id(data.next_meeting.meeting_id),starts_at:timestamp(data.next_meeting.starts_at)};
  return {rm,categories,accounts,next_meeting:next,today:day(data.today),call_id:id(value.call_id),as_of:timestamp(value.as_of)};
}

// What a card shows: on with its end, off, or on for every meeting under the notice.
export function cardState(card) {
  if(!card.gated)return {on:true,label:'Always shared',switchable:false};
  if(!card.withdrawable)return {on:!!card.grant,label:card.grant?'On for every meeting':'On when a meeting is confirmed',switchable:false};
  return {on:!!card.grant,label:card.grant?'On':'Off',switchable:true};
}
export function untilText(grant,format) {
  if(!grant)return '';
  if(grant.basis==='notice')return 'Under the meeting notice';
  if(grant.until==='meeting')return grant.expires_at?`Until your meeting ends (at the latest ${format(grant.expires_at)})`:'Until your meeting ends';
  if(grant.until==='request')return 'Until your meeting request is answered, and then until that meeting ends';
  return grant.expires_at?`Until ${format(grant.expires_at)}`:'No end date';
}
const ACCOUNT={savings:'Savings',current:'Current',multi_currency:'Multi-currency'};
export const accountName=a=>`${ACCOUNT[a.type]||a.type} ${a.number}`;
// One list in a sentence: the first word keeps its capital, the rest are lower case.
const listed=labels=>labels.map((l,i)=>i?l[0].toLowerCase()+l.slice(1):l).join(', ');
export function scopeText(category,scope,accounts=[]) {
  if(!scope)return 'Everything in this category (an earlier consent)';
  if(category==='transaction_insights') {
    const which=scope.accounts==='all'?'All your accounts and cards':scope.accounts.map(n=>{const a=accounts.find(x=>x.account_id===n);return a?accountName(a):'an account';}).join(', ')+' (no card spending)';
    return `${which} · last ${scope.lookback_months} months`;
  }
  if(category==='chat_topics')return scope.topic_areas.length===Object.keys(TOPIC_AREAS).length?'All topic areas':listed(scope.topic_areas.map(a=>TOPIC_AREAS[a]));
  return '';
}
// Said before she switches a category off, so she knows what changes at once.
export function revokeConsequence(category,rm) {
  const who=rm||'Your relationship manager';
  if(category==='transaction_insights')return `${who} stops seeing your transaction insights at once. Briefs for your meetings will say you have not shared them. Nothing already in your accounts changes.`;
  if(category==='chat_topics')return `${who} stops seeing topics picked up from your chats, and they are removed at once. Topics you shared yourself stay until you delete them.`;
  return '';
}

// The ledger target of a switch (actions.consent_grant_target checks it again in SQL).
export function grantTarget(card,choice,sharing) {
  if(!SWITCHABLE.includes(card.code)||!sharing.rm||!card.notice_version)throw invalid();
  const scope=card.code==='transaction_insights'
    ?{accounts:choice.accounts==='all'||!choice.accounts?.length?'all':[...choice.accounts].sort((a,b)=>a-b),lookback_months:choice.lookback_months||6}
    :{topic_areas:Object.keys(TOPIC_AREAS).filter(a=>(choice.topic_areas||[]).includes(a))};
  if(card.code==='chat_topics'&&!scope.topic_areas.length)throw new Error('Choose at least one topic area.');
  const base={category:card.code,recipient:sharing.rm.advisor_id,scope,notice_version:card.notice_version,until:choice.until};
  if(choice.until==='date') {
    if(!DAY.test(choice.expires_on||''))throw new Error('Choose an end date.');
    return {...base,expires_on:choice.expires_on};
  }
  if(choice.until==='meeting') {
    if(!sharing.next_meeting)throw new Error('You have no confirmed meeting to share until.');
    return {...base,meeting_id:sharing.next_meeting.meeting_id};
  }
  throw invalid();  // until a request is answered is only offered with a meeting request in chat
}
// The latest end date she can choose: max_months after today.
export function latestEnd(today,months) {
  const d=new Date(`${today}T00:00:00Z`);
  d.setUTCMonth(d.getUTCMonth()+months);
  return d.toISOString().slice(0,10);
}
export function plusDays(today,days) {const d=new Date(`${today}T00:00:00Z`);d.setUTCDate(d.getUTCDate()+days);return d.toISOString().slice(0,10);}

// The form a card opens with: her current choice when the switch is on, else every account and 6 months (or every topic
// area) until 3 months from today (never past the category's limit), or until her next meeting when she has one.
export function defaultChoice(card,sharing) {
  const scope=card.grant?.scope;
  const last=latestEnd(sharing.today,card.max_months||12),end=plusDays(sharing.today,90);
  const until=card.grant?.until==='meeting'&&sharing.next_meeting?'meeting':'date';
  if(card.code==='transaction_insights')return {accounts:scope?.accounts||'all',lookback_months:scope?.lookback_months||6,until,expires_on:end<last?end:last};
  return {topic_areas:scope?.topic_areas||Object.keys(TOPIC_AREAS),until,expires_on:end<last?end:last};
}
// What the review line says before she confirms a switch.
export function choiceSummary(card,choice,sharing,format) {
  const who=sharing.rm?.name||'your relationship manager';
  const scope=grantTarget(card,choice,sharing).scope;
  const until=choice.until==='meeting'?`until your meeting on ${format(sharing.next_meeting.starts_at)} ends`:`until ${format(`${choice.expires_on}T00:00:00+08:00`)}`;
  return `${who} will see your ${card.label.toLowerCase()} (${scopeText(card.code,scope,sharing.accounts)}) ${until}.`;
}
export const alwaysShared=sharing=>sharing.categories.filter(c=>!c.gated);
export const alwaysLine=sharing=>listed(alwaysShared(sharing).map(c=>c.label));
// The top-level list (8 Nov redesign): one row per category with a status and one line of what is shared.
export function rowState(card) {
  const state=cardState(card);
  if(!card.withdrawable)return {...state,pill:'For meetings',tone:'neutral'};
  return {...state,pill:state.on?'Shared':'Not shared',tone:state.on?'on':'off'};
}
function untilShort(grant,format) {
  if(!grant||grant.basis==='notice')return '';
  if(grant.until==='meeting')return 'until your meeting ends';
  if(grant.until==='request')return 'until your meeting request is answered';
  return grant.expires_at?`until ${format(grant.expires_at)}`:'';
}
// topics: her talking points ({points}) for the chat topics row; rm: her relationship manager's first name.
export function rowLine(card,sharing,rm,format,topics=null) {
  const who=rm||'Your relationship manager',grant=card.grant,until=untilShort(grant,format);
  const join=(...parts)=>parts.filter(Boolean).join(' · ');
  if(card.code==='meeting_recording')return grant?'Part of every confirmed meeting':'Starts when a meeting is confirmed';
  if(card.code==='chat_topics') {
    const shown=topics?.points?.length??null,count=n=>`${n} topic${n===1?'':'s'}`;
    if(grant)return join(shown===null?null:count(shown),until);
    const own=topics?.points?.filter(p=>p.origin==='confirmed').length||0;
    return own?`Only topics you shared yourself (${own})`:`${who} doesn't see topics from your chats`;
  }
  if(card.code==='transaction_insights')return grant?join(scopeText(card.code,grant.scope,sharing.accounts),until):`${who} can't see your spending`;
  return grant?join(until):card.description;
}
export const openAsks=(asks,category)=>asks.filter(a=>a.status==='open'&&(!category||a.category===category));
// Delete all my data (0113): a request to the data protection team, answered within 10 business days.
export const DELETE_ALL={category:'data_deletion',summary:'Please delete all my data.',urgency:'normal',details:{}};
export const DELETE_ALL_NOTE='This sends a request to our data protection team. They decide what the law requires the bank to keep and reply within 10 business days. Nothing is deleted yet.';

// Who looked (0112): rows of ids, codes and dates only; the words come from here.
const ALWAYS={profile_basics:'your profile',products:'your products',requests_and_meetings:'your requests and meetings',compliance_status:'your document review status'};
const GATED_WORDS={transaction_insights:'your transaction insights',chat_topics:'topics from your chats',meeting_recording:'your meeting recording'};
export function parseAccessLog(value) {
  if(!Array.isArray(value?.data)||value.data.length>200)throw invalid();
  return {call_id:id(value.call_id),as_of:timestamp(value.as_of),rows:value.data.map(r=>{
    if(!['read','not_shared','refused'].includes(r?.outcome)||!['current_rm','former_rm','not_your_rm'].includes(r.relation))throw invalid();
    if(r.basis!==null&&!['consent','client_shared','always_shared'].includes(r.basis))throw invalid();
    if(r.purpose!==null&&!['meeting_prep','client_view'].includes(r.purpose))throw invalid();
    return {call_id:maybeId(r.call_id),at:timestamp(r.at),advisor_id:maybeId(r.advisor_id),relation:r.relation,tool:maybeCode(r.tool),category:maybeCode(r.category),
      purpose:r.purpose,meeting_id:maybeId(r.meeting_id),outcome:r.outcome,basis:r.basis,grant_id:maybeId(r.grant_id)};
  })};
}
export function accessLine(row,rm) {
  const who=row.relation==='current_rm'?(rm||'Your relationship manager'):row.relation==='former_rm'?'Your previous relationship manager':'A relationship manager who does not look after you';
  const what=GATED_WORDS[row.category]||ALWAYS[row.category]||'your records';
  const why=row.purpose==='meeting_prep'?' to prepare for a meeting':row.purpose==='client_view'?' while looking at your overview':'';
  if(row.outcome==='refused')return `${who} tried to open your records. It was refused and recorded.`;
  // 0110: a brief re-checks whether the topics it used are still shown (ids and dates only, never the topics themselves).
  if(row.tool==='rm.point_status')return `${who}'s meeting brief checked that the topics it uses are still shared.`;
  if(row.outcome==='not_shared')return `${who} looked for ${what}${why}, and saw only that you have not shared it.`;
  return `${who} saw ${what}${why}${row.basis==='client_shared'?' (topics you shared yourself)':row.basis==='always_shared'?' (always shared)':''}.`;
}
// The same look within the same minute is one line with a count (a brief reads each record once per section).
export function accessGroups(rows,rm) {
  const groups=new Map();
  for(const row of rows) {
    const line=accessLine(row,rm),key=`${row.at.slice(0,16)}|${line}`,seen=groups.get(key);
    if(seen)seen.count+=1;else groups.set(key,{key,line,at:row.at,count:1});
  }
  return [...groups.values()];
}

// Her RM's asks (0113): she answers by switching the category on, or with Not now (consent.decline_ask).
export function parseAsks(value) {
  if(!Array.isArray(value?.data)||value.data.length>50)throw invalid();
  return value.data.map(a=>{
    if(!SWITCHABLE.includes(a?.category)||!Object.hasOwn(ASK_REASONS,a.reason)||!['open','shared','declined','lapsed'].includes(a.status))throw invalid();
    return {ask_id:id(a.ask_id),category:a.category,category_label:text(a.category_label,80),reason:a.reason,advisor_id:id(a.advisor_id),rm_name:maybeText(a.rm_name,200),
      meeting_id:maybeId(a.meeting_id),meeting_starts_at:maybeTime(a.meeting_starts_at),status:a.status,asked_at:timestamp(a.asked_at),lapses_at:timestamp(a.lapses_at),
      closed_at:maybeTime(a.closed_at),reference:maybeText(a.reference,20)};
  });
}
export function askLine(ask,format) {
  const who=ask.rm_name||'Your relationship manager';
  const why=ask.reason==='meeting_prep'&&ask.meeting_starts_at?`to prepare for your meeting on ${format(ask.meeting_starts_at)}`:ASK_REASONS[ask.reason];
  return `${who} asked to see your ${ask.category_label.toLowerCase()} ${why}.`;
}

// Ledger records on the Actions page: a v2 grant (no request_id), and her answer to an ask.
export function parseGrantV2Target(value) {
  if(!value||!SWITCHABLE.includes(value.category)||!Object.hasOwn(UNTIL,value.until)||value.purpose!=='rm_brief')throw invalid();
  return {category:value.category,recipient:id(value.recipient),scope:parseScope(value.category,value.scope),notice_version:notice(value.notice_version),until:value.until,
    expires_on:value.expires_on===null||value.expires_on===undefined?null:day(value.expires_on),meeting_id:maybeId(value.meeting_id),
    purpose:'rm_brief',categories:[value.category],category_label:text(value.category_label,80),recipient_name:text(value.recipient_name,200),
    replaces:maybeId(value.replaces),expires_at:maybeTime(value.expires_at)};
}
export function parseGrantV2Result(target,value) {
  const result=parseGrantV2Target(value);
  if(value.status!=='active'||result.category!==target.category||result.recipient!==target.recipient||result.until!==target.until||
    JSON.stringify(result.scope)!==JSON.stringify(target.scope))throw invalid();
  return {...result,grant_id:id(value.grant_id),status:'active',active_until:maybeTime(value.active_until)};
}
export function parseAskTarget(value) {
  if(!value||!SWITCHABLE.includes(value.category))throw invalid();
  return {ask_id:id(value.ask_id),category:value.category,recipient:id(value.recipient)};
}
export function parseAskResult(target,value) {
  if(value?.status!=='declined'||value.ask_id!==target.ask_id)throw invalid();
  return {...target,status:'declined'};
}
export function grantSentence(target) {
  return `Shared ${target.category_label.toLowerCase()} with ${target.recipient_name}`;
}

async function read(path,token,signal,parse) {
  const response=await fetch(`${API_URL}${path}`,{headers:{Authorization:`Bearer ${token}`},signal});
  let body;
  try {body=await response.json();}
  catch(error) {if(error instanceof SyntaxError)throw invalid();throw error;}
  if(!response.ok)throw new Error(typeof body?.error?.message==='string'&&body.error.message.trim()?body.error.message:'Data sharing could not be loaded. Refresh to try again.');
  return parse(body);
}
export const loadSharing=(token,signal)=>read('/consent/sharing',token,signal,parseSharing);
export const loadAccessLog=(token,signal)=>read('/consent/access-log',token,signal,parseAccessLog);
export const loadAsks=(token,signal)=>read('/consent/asks',token,signal,parseAsks);
