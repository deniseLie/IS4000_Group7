// My requests (TK1/TK2) and request categories (F20). Tickets are created only through a reviewed ticket.create action.
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const API_URL=import.meta.env?.VITE_API_URL || '/api';
export const STATUSES={submitted:'Submitted',in_review:'In review',waiting_on_client:'Waiting for you',resolved:'Resolved',closed:'Closed'};
const KINDS=['text','amount','date','date_past','choice','card'];
function invalid() {return new Error('The recorded request could not be verified. Reload My requests.');}
function id(value) {if(typeof value!=='string'||!UUID.test(value))throw invalid();return value;}
function text(value,max) {if(typeof value!=='string'||!value.trim()||value.length>max)throw invalid();return value;}
function time(value) {if(typeof value!=='string'||!/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(value)||!Number.isFinite(Date.parse(value)))throw invalid();return value;}
function key(value) {if(typeof value!=='string'||!/^[a-z_]{3,40}$/.test(value))throw invalid();return value;}
function details(value) {
  if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).length>10)throw invalid();
  return Object.fromEntries(Object.entries(value).map(([name,item])=>{if(!/^[a-z_]{2,30}$/.test(name)||typeof item!=='string'||item.length>300)throw invalid();return [name,item];}));
}

export function parseTicketTarget(value) {
  if(!['low','normal','high'].includes(value?.urgency)||!Number.isSafeInteger(value.target_hours)||value.target_hours<1)throw invalid();
  return {category:key(value.category),summary:text(value.summary,500),urgency:value.urgency,details:details(value.details),
    category_label:text(value.category_label,80),owner_team:text(value.owner_team,80),decided_by:text(value.decided_by,120),target_hours:value.target_hours};
}
export function parseTicketResult(value) {
  if(!Object.keys(STATUSES).includes(value?.status)||typeof value.created!=='boolean'||typeof value.reference!=='string'||!/^TCK-\d{6,}$/.test(value.reference))throw invalid();
  return {ticket_id:id(value.ticket_id),reference:value.reference,category:key(value.category),status:value.status,owner_team:text(value.owner_team,80),target_at:time(value.target_at),created:value.created};
}
export function ticketResultMatches(target,result) {return target.category===result.category&&target.owner_team===result.owner_team;}

export function parseTicket(value) {
  if(!Object.keys(STATUSES).includes(value?.status)||!['low','normal','high','urgent'].includes(value.urgency)||typeof value.overdue!=='boolean'
    ||!['form','chat','handoff','rm_request','seed'].includes(value.source)||typeof value.reference!=='string'||!/^TCK-\d{6,}$/.test(value.reference))throw invalid();
  const update=value.last_update;
  if(update!==null&&(!update||!Object.keys(STATUSES).includes(update.status)||update.note!==null&&typeof update.note!=='string'))throw invalid();
  return {ticket_id:id(value.ticket_id),reference:value.reference,category:key(value.category),category_label:text(value.category_label,80),summary:text(value.summary,500),
    details:details(value.details),urgency:value.urgency,status:value.status,owner_team:text(value.owner_team,80),decided_by:text(value.decided_by,120),source:value.source,
    created_at:time(value.created_at),updated_at:time(value.updated_at),target_at:time(value.target_at),overdue:value.overdue,
    last_update:update&&{at:time(update.at),status:update.status,note:update.note},attachments:attachments(value.attachments)};
}
// Documents attached to an RM ticket (0082): id and file name only. Older servers send none.
function attachments(value) {
  if(value===undefined||value===null)return [];
  if(!Array.isArray(value)||value.length>20)throw invalid();
  return value.map(item=>({document_id:id(item?.document_id),file_name:text(item?.file_name,255)}));
}
export function parseTickets(value) {
  if(!Array.isArray(value?.data)||value.data.length>200)throw invalid();
  const tickets=value.data.map(parseTicket);
  if(new Set(tickets.map(item=>item.reference)).size!==tickets.length)throw invalid();
  return tickets;
}
export function parseCategories(value) {
  if(!Array.isArray(value)||value.length>50)throw invalid();
  return value.map(item=>{
    if(!Array.isArray(item?.fields)||item.fields.length>10||typeof item.customer_raisable!=='boolean'||!Number.isSafeInteger(item.target_hours))throw invalid();
    const fields=item.fields.map(field=>{
      if(!/^[a-z_]{2,30}$/.test(field?.key??'')||!KINDS.includes(field.kind)||typeof field.required!=='boolean')throw invalid();
      return {key:field.key,label:text(field.label,120),kind:field.kind,required:field.required,choices:Array.isArray(field.choices)?field.choices.map(c=>text(c,40)):null};
    });
    return {category:key(item.category),label:text(item.label,80),description:text(item.description,400),owner_team:text(item.owner_team,80),
      decided_by:text(item.decided_by,120),target_hours:item.target_hours,fields,documents:Array.isArray(item.documents)?item.documents.map(d=>text(d,200)):[],customer_raisable:item.customer_raisable};
  });
}

// The next step a customer sees, in plain language. The bank decides; this only describes the recorded status.
export function nextStep(ticket) {
  if(ticket.status==='waiting_on_client')return ticket.last_update?.note||'The bank needs something from you. Use Talk to a person if it is unclear.';
  if(ticket.status==='resolved')return 'The bank marked this resolved. If something is still wrong, raise a new request or use Talk to a person.';
  if(ticket.status==='closed')return 'Closed. Nothing more is needed from you.';
  if(ticket.overdue)return `This is past the ${ticket.owner_team.toLowerCase()}'s target time. Use Talk to a person to ask your relationship manager to follow up.`;
  const team=/^your /i.test(ticket.owner_team)?ticket.owner_team:`The ${ticket.owner_team.toLowerCase()}`;
  return `No action needed from you. ${team} will review it and update this request.`;
}
// What the request is about, in the client's words; internal wording from automatic handovers is replaced.
// Handover tickets (0082) start with a topic and may end with her earlier messages for the RM; she sees her question.
export function ticketSummary(ticket) {
  const text=ticket.summary||'';
  if(/^An assistant section was withheld/i.test(text))return 'Part of an answer from the assistant could not be checked, so it was passed to your relationship manager.';
  return text.replace(/^\[[^\]]{1,60}\]\s*/,'').replace(/^Client question for you:\s*/i,'').split(' | Earlier in this chat:')[0];
}
// The topic an RM handover ticket was opened with, shown as a small label; null for other tickets.
export function ticketTopic(ticket) {
  const found=/^\[([^\]]{1,60})\]/.exec(ticket?.summary||'');
  return ticket?.category==='rm_handover'&&found?found[1]:null;
}
// What the client sees when a request could not be recorded (CR9): nothing was sent, and another way to get help.
export function requestFailed(failure) {
  const detail=failure?.message&&!/^(?:Failed to fetch|NetworkError|Load failed)/i.test(failure.message)?` (${failure.message.replace(/\.$/,'')})`:'';
  return `We couldn't record your request just now${detail}, so nothing has been sent. Please try again in a few minutes, or use Talk to a person to reach your relationship manager.`;
}

// Which raisable category a chat question points to; null when none clearly does.
export function ticketIntent(question) {
  const q=String(question||'').toLowerCase();
  if(/\b(?:increase|raise|higher|lower|reduce|change|up)\b[^.?!]{0,40}\b(?:credit\s+)?(?:card\s+)?limit\b|\blimit\s+(?:increase|change)\b/.test(q))return 'card_limit_change';
  // Statements (CR15): the last 3 full months are on Overview; older, certified or paper copies are a request.
  if(/\b(?:e-?)?statements?\b/.test(q)&&!/\b(?:review|compliance|upload|submit)\b/.test(q))
    return /\b(?:older|old|certified|paper|copy|copies|stamped|last\s+year|(?:20[0-9]{2})|(?:[4-9]|1[0-2])\s+months?|six|twelve)\b/.test(q)?'statement_request':'statements';
  if(/\b(?:status|update|progress)\b[^.?!]{0,30}\b(?:my\s+)?(?:ticket|request|case)s?\b|\bmy\s+(?:tickets|open requests)\b/.test(q))return 'status';
  return null;
}

async function body(response) {
  let value;
  try {value=await response.json();} catch(error) {if(error instanceof SyntaxError)throw invalid();throw error;}
  if(!response.ok)throw new Error(typeof value?.error?.message==='string'&&value.error.message.trim()?value.error.message:'Your requests are unavailable. Please retry.');
  return value;
}
export async function loadTickets(token,signal) {return parseTickets(await body(await fetch(`${API_URL}/tickets`,{headers:{Authorization:`Bearer ${token}`},signal})));}
export async function loadCategories(token,signal) {
  return parseCategories(await body(await fetch(`${API_URL}/tickets/categories`,{headers:token?{Authorization:`Bearer ${token}`}:{},signal})));
}
