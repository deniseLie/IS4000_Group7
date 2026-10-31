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
  if(ticket.overdue)return `This is past the ${ticket.owner_team.toLowerCase()}'s target time. Contact the bank through its official support channel to follow up with the responsible team.`;
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
  return `We couldn't record your request just now${detail}, so nothing has been sent. Please try again in a few minutes, or use the official support channel for the responsible team.`;
}

// Which raisable category a chat question points to; null when none clearly does.
export function ticketIntent(question) {
  const q=String(question||'').toLowerCase();
  // A question about a previously submitted request is never another request.
  if(isExistingRequestStatus(q))return 'status';
  if(!isConceptQuestion(q)&&(/\b(?:increase|raise|higher|lower|reduce|change|up)\b[^.?!]{0,40}\b(?:credit\s+)?(?:card\s+)?limit\b|\b(?:credit\s+)?(?:card\s+)?limit\b[^.?!]{0,40}\b(?:increase|increased|raise|raised|change)\b/.test(q)))return 'card_limit_change';
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

// CR11: the card limit request carries the latest amount she stated in the chat, so a correction
// ("actually make it 12,000") replaces the earlier figure instead of both being sent.
const AMOUNT=/(?:s?\$|sgd\s*)?(\d{1,3}(?:,\d{3})+|\d{4,7}|\d{1,3}(?=\s*k\b))(?:\.\d{2})?\s*(k\b)?/gi;
// Whether an assistant response is an authorised, in-progress *client* card-limit
// conversation. Enables the request button after short follow-ups such as "travel".
export function isLimitWorkflowReply(message) {
  if(!message||message.role!=='assistant'||message.error||message.source_check==='rejected')return false;
  if(!message.sections?.some(section=>section.domain==='retail'))return false;
  return /\b(?:requested new limit|request ready for your review|which card is this for|temporary change end)\b/i.test(message.content||'');
}

export function isConceptQuestion(question) {
  return /^\s*(?:what\s+(?:is|are|does|do)\b|(?:what(?:'s|s)|wats)\s+the\s+difference\b|how\s+does\b|explain\b|define\b|difference\s+between\b)/i.test(String(question||''));
}

// CR11: Convert the *client's own* saved chat turns into a draft, not just a text
// summary. In particular, a year supplied in one answer and a day/month supplied
// in the next must become one explicit ISO date for the native date input.
// This is a UI convenience only: the client must still review the form and the
// backend validates every submitted field. Never fill bank-recorded card details.
const MONTHS={jan:1,january:1,feb:2,february:2,mar:3,march:3,apr:4,april:4,may:5,jun:6,june:6,jul:7,july:7,aug:8,august:8,sep:9,sept:9,september:9,oct:10,october:10,nov:11,november:11,dec:12,december:12};
const DAY_MONTH=/\b(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\b(?:\s+(20\d{2}))?/i;
const YEAR_REPLY=/^\s*(20\d{2})\s*[.!]?\s*$/;
const CARD_REPLY=/^\s*(?:the\s+|my\s+)?(travel|signature)(?:\s+(?:card|one))?\s*[.!]?\s*$/i;
const LIMIT_OTHER=/\b(?:account balance|card balance|statements?|compliance|upload|mortgage|loans?|fraud|suspicious|lost card|appointments?|book (?:a |my )?meeting|annual fee|ticket status|request status|what(?:'s| is) my (?:current )?(?:credit |card )?limit)\b/i;

function validISO(year,month,day) {
  const y=Number(year),m=Number(month),d=Number(day);
  if(!Number.isInteger(y)||y<2000||y>2099||!Number.isInteger(m)||m<1||m>12||!Number.isInteger(d)||d<1||d>31)return null;
  const date=new Date(Date.UTC(y,m-1,d));
  if(date.getUTCFullYear()!==y||date.getUTCMonth()!==m-1||date.getUTCDate()!==d)return null;
  return `${year}-${String(m).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
}
function displayISO(iso) {
  const [year,month,day]=iso.split('-').map(Number);
  const label=['January','February','March','April','May','June','July','August','September','October','November','December'][month-1];
  return `${day} ${label} ${year}`;
}
function requestAmount(text) {
  const matches=[];
  for(const match of String(text||'').matchAll(AMOUNT)) {
    const prefix=text.slice(Math.max(0,match.index-18),match.index).toLowerCase();
    const hinted=/\$|sgd/i.test(match[0])||/k\b/i.test(match[0])||/,/.test(match[1])||/\b(?:limit|to|make it|make that|instead|request|amount)\s*$/.test(prefix);
    if(!hinted)continue;
    const value=Number(match[1].replaceAll(',',''))*(match[2]?1000:1);
    if(value>=500&&value<=1000000)matches.push(value);
  }
  return matches;
}
// messages contains both user and assistant turns to distinguish "travel" (card
// choice) from a travel reason and "2028" (date reply) from a requested limit.
export function limitRequestPrefill(messages) {
  const turns=Array.isArray(messages)?messages.map(item=>typeof item==='string'?{role:'user',content:item}:item).filter(item=>item&&['user','assistant'].includes(item.role)&&typeof item.content==='string'):[];
  const start=turns.findLastIndex(item=>item.role==='user'&&LIMIT_START.test(item.content)&&!isConceptQuestion(item.content));
  if(start<0)return {summary:'',details:{}};
  let relevant=turns.slice(start);
  // Never carry a previous client's unrelated enquiry into a new request.
  const other=relevant.findIndex((item,i)=>i>0&&item.role==='user'&&LIMIT_OTHER.test(item.content)&&!LIMIT_START.test(item.content));
  if(other>=0)relevant=relevant.slice(0,other);
  const users=relevant.filter(item=>item.role==='user');
  if(users.length>14)return {summary:'',details:{}};
  let amount=null,prior=[],duration=null,year=null,dayMonth=null,cardLabel=null,reason=null;
  for(let i=0;i<relevant.length;i++) {
    const entry=relevant[i];if(entry.role!=='user')continue;
    const value=entry.content.trim();
    const previous=i>0&&relevant[i-1].role==='assistant'?relevant[i-1].content:'';
    const amounts=requestAmount(value);
    for(const n of amounts){if(amount!==null&&amount!==n)prior.push(amount);amount=n;}
    if(/\b(?:temporary|temporarily|until|through|only for (?:my |the )?trip)\b/i.test(value))duration='temporary';
    else if(/\bpermanent(?:ly)?\b/i.test(value)){duration='permanent';year=null;dayMonth=null;}
    const selected=CARD_REPLY.exec(value);
    const isCardAnswer=!!selected&&/\b(?:which card|card is this for|card should|choose a card)\b/i.test(previous);
    if(/\b(?:travell?ing|trip|holiday|vacation|overseas)\b/i.test(value)||/\btravel\b/i.test(value)&&!isCardAnswer)reason='Travel';
    if(isCardAnswer)cardLabel=selected[1].toLowerCase()==='travel'?'Travel Card':'Signature Card';
    else if(/\b(?:Travel Card|Signature Card)\b/i.test(value))cardLabel=/travel/i.test(value)?'Travel Card':'Signature Card';
    // A bare year is a date answer only after the assistant asked for it.
    const bareYear=YEAR_REPLY.exec(value);
    if(duration==='temporary'&&bareYear&&/\b(?:date|year|end)\b/i.test(previous))year=bareYear[1];
    // A bare day/month is a date answer only in the date-collection part of the flow.
    const match=DAY_MONTH.exec(value);
    const dateQuestion=/\b(?:date|year|end|month)\b/i.test(previous);
    const explicitEnd=/\b(?:until|through|ending|end(?:s|ing)? (?:on|at))\b/i.test(value);
    if(duration==='temporary'&&match&&(explicitEnd||dateQuestion||match[3]||dayMonth&&/\b(?:actually|instead|correction)\b/i.test(value))) {
      const parsedYear=match[3]||year;
      const candidate=parsedYear?validISO(parsedYear,MONTHS[match[2].toLowerCase()],match[1]):null;
      dayMonth={day:Number(match[1]),month:match[2],year:parsedYear||null,date:candidate};
      if(match[3])year=match[3];
    }
  }
  // The user may give the day/month first and the year in a later reply.
  if(dayMonth&&!dayMonth.date&&year)dayMonth={...dayMonth,year,date:validISO(year,MONTHS[dayMonth.month.toLowerCase()],dayMonth.day)};
  const details={};
  if(amount!==null)details.new_limit=String(amount);
  if(duration)details.duration=duration;
  if(duration==='temporary'&&dayMonth?.date)details.until=dayMonth.date;
  if(reason)details.reason=reason;
  const note=[...new Set(prior.filter(n=>n!==amount))];
  const summary=[];
  if(amount!==null)summary.push(`Requested new limit: SGD ${amount.toLocaleString('en-SG')}${note.length?` (corrected from SGD ${note.map(n=>n.toLocaleString('en-SG')).join(', SGD ')})`:''}`);
  if(duration)summary.push(duration==='temporary'?'Temporary request':'Permanent request');
  if(cardLabel)summary.push(cardLabel);
  if(dayMonth?.date)summary.push(`End date: ${displayISO(dayMonth.date)}`);
  else if(dayMonth)summary.push(`End date to confirm: ${dayMonth.day} ${dayMonth.month}${year?` ${year}`:' (year not confirmed)'}`);
  else if(year&&duration==='temporary')summary.push(`End date incomplete: year ${year}`);
  if(reason)summary.push(`Reason: ${reason}`);
  return {summary:(summary.join('; ')+(summary.length?'.':''))||users.at(-1)?.content.slice(0,500)||'',details,selectedCardLabel:cardLabel};
}

const LIMIT_START=/\b(?:increase|raise|higher|lower|reduce|change|adjust|temporary|temporarily|boost|up)\b[^.?!]{0,65}\b(?:credit\s+)?(?:card\s+)?limit\b|\b(?:credit\s+)?(?:card\s+)?limit\b[^.?!]{0,55}\b(?:increase|increased|raise|raised|higher|change|adjust|to\s+(?:\$|sgd|\d))\b/i;

export function limitSummary(userMessages) {
  const messages=userMessages.map(value=>String(value||''));
  const start=messages.findLastIndex(text=>LIMIT_START.test(text));
  const relevant=(start>=0?messages.slice(start):messages.slice(-4)).slice(-10);
  const amounts=[];
  for(const text of relevant)for(const match of text.matchAll(AMOUNT)) {
    const prefix=text.slice(Math.max(0,match.index-18),match.index).toLowerCase();
    const hinted=/\$|sgd/i.test(match[0])||/k\b/i.test(match[0])||/,/.test(match[1])||/\b(?:limit|to|make it|make that|instead|request|amount)\s*$/.test(prefix);
    if(!hinted)continue; // '20 December 2026' is not a requested limit.
    const value=Number(match[1].replaceAll(',',''))*(match[2]?1000:1);
    if(value>=500&&value<=1000000)amounts.push(value);
  }
  const last=relevant.length?relevant[relevant.length-1]:'';
  if(!amounts.length)return last.slice(0,500);
  const latest=amounts[amounts.length-1];
  const earlier=[...new Set(amounts.slice(0,-1).filter(value=>value!==latest))];
  const note=earlier.length?` (corrected from SGD ${earlier.map(value=>value.toLocaleString('en-SG')).join(', SGD ')})`:'';
  const selected=[...relevant].reverse().find(text=>/\b(?:my |the )?(?:Travel|Signature) Card\b/i.test(text)||/^\s*(?:travel|signature)(?: card)?\s*[.!]?$/i.test(text));
  const card=selected&&/travel/i.test(selected)?' Travel Card;':selected&&/signature/i.test(selected)?' Signature Card;':'';
  const temporary=relevant.some(text=>/\b(?:temporary|temporarily|until)\b/i.test(text));
  const end=[...relevant].reverse().map(text=>/\b(?:until|through|ending)\s+(\d{1,2}\s+[a-z]+(?:\s+20\d{2})?)/i.exec(text)).find(Boolean);
  const detail=(temporary?' Temporary request;':'')+(end?` End date: ${end[1]};`:'')+card;
  return `Requested new limit: SGD ${latest.toLocaleString('en-SG')}${note}.${detail} ${last}`.slice(0,500).trim();
}

export function isExistingRequestStatus(input) {
  const q=String(input||'').toLowerCase();
  const status=/\b(?:status|progress|update|approved|rejected|approval|decision|accepted|processed|completed|gone through)\b/.test(q);
  const existing=/\b(?:my|the|that|previous|existing|earlier)\b.{0,90}\b(?:request|ticket|case|(?:card |credit )?limit (?:increase|change))\b|\b(?:request|ticket|case)s?\b.{0,60}\b(?:status|progress|approved|decision|update)\b|\btck-\d{6,}\b/.test(q);
  const specific=/\b(?:has|have|did|is|was)\s+(?:the bank\s+)?(?:my\s+)?(?:card\s+|credit\s+)?limit\s+(?:increase|change|request)\b/.test(q);
  return status&&(existing||specific)&&!/^\s*how (?:can|do) i (?:get|become) approved/.test(q);
}
