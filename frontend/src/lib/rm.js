// Relationship manager workspace (Persona 2): API calls and the pure helpers behind the RM pages.
// The backend checks the signed token's app_metadata role and the client book on every call; this module only decides
// what the browser shows. Nothing here sends anything to a client: writes go out only from an explicit approval.
import {readEvents,redact} from './chatTools.js';

const API=import.meta.env?.VITE_API_URL||'/api';

export function isRm(session) {return session?.user?.app_metadata?.demobank_role==='rm';}
export function newId() {return globalThis.crypto.randomUUID();}

async function call(method,path,token,body,signal) {
  const response=await fetch(`${API}/rm${path}`,{method,signal,headers:{Authorization:`Bearer ${token}`,...(body?{'Content-Type':'application/json'}:{})},
    body:body?JSON.stringify(body):undefined});
  const data=await response.json().catch(()=>null);
  if(!response.ok){const error=new Error(data?.error?.message||'Your records could not be read right now. Please retry.');error.code=data?.error?.code;throw error;}
  return data;
}
export const rmGet=(path,token,signal)=>call('GET',path,token,null,signal);
export const rmPost=(path,body,token,signal)=>call('POST',path,token,body,signal);
export const rmPut=(path,body,token,signal)=>call('PUT',path,token,body,signal);
export const rmPatch=(path,body,token,signal)=>call('PATCH',path,token,body,signal);
export const rmDelete=(path,token,signal)=>call('DELETE',path,token,null,signal);

// The assistant streams status lines, then one structured answer (or an error message).
export async function askAssistant(body,token,onStatus,signal) {
  const response=await fetch(`${API}/rm/assistant`,{method:'POST',signal,headers:{Authorization:`Bearer ${token}`,'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!response.ok){const data=await response.json().catch(()=>null);throw new Error(data?.error?.message||'The assistant is unavailable. Please retry.');}
  let answer=null;
  await readEvents(response.body,(type,data)=>{if(type==='status')onStatus?.(data);if(type==='answer')answer=JSON.parse(data);});
  if(!answer)throw new Error('No answer was received. Please retry.');
  return answer;
}

// Every draft in a reply: one (`draft`), or one per client when the RM asked for a message to several (`drafts`, R21).
export const allDrafts=reply=>reply?.drafts?.length?reply.drafts:reply?.draft?[reply.draft]:[];

// Plain text of a reply, for the next turn's history (summary plus item texts, bounded).
export function replyText(reply) {
  if(!reply)return '';
  const parts=[reply.summary||'',...(reply.sections||[]).flatMap(s=>[s.heading,...s.items.map(i=>`- ${i.text}`)])];
  for(const d of allDrafts(reply))parts.push(`Draft${d.client?` to ${d.client}`:''}: ${d.text}`);
  if(reply.documentation?.draft?.summary)parts.push(`Documentation: ${reply.documentation.draft.summary}`);
  return parts.filter(Boolean).join('\n').slice(0,3000);
}

// History for the next request: each turn keeps the client it was about, so the server can drop other clients' turns.
export function historyFor(messages) {
  const turns=messages.filter(m=>!m.error&&!m.pending&&(m.role==='user'?m.content:m.reply)).map(m=>({role:m.role,
    content:redact(m.role==='user'?m.content:replyText(m.reply)).slice(0,6000),client_id:m.client_id??null}));
  while(turns.length>30||turns.reduce((n,t)=>n+t.content.length,0)>24000)turns.shift();
  while(turns.length&&turns[0].role!=='user')turns.shift();
  return turns;
}

// The context after a reply: a new client clears drafts and documentation that belong to another client.
export function contextAfter(previous,reply) {
  const next=reply?.context||{};
  const client=next.client_id??previous.client_id??null;
  const same=item=>item&&item.customer_id===client?item:null;
  return {client_id:client,ticket_ref:next.ticket_ref??(client===previous.client_id?previous.ticket_ref:null)??null,
    appointment_id:next.appointment_id??(client===previous.client_id?previous.appointment_id:null)??null,
    note_id:next.note_id??(client===previous.client_id?previous.note_id:null)??null,
    draft:next.draft?{...next.draft}:same(previous.draft),
    documentation:next.documentation?{...next.documentation}:same(previous.documentation)};
}
// What the request body's context may carry (the server validates every field again).
export function requestContext(context) {
  const doc=context.documentation;
  return {client_id:context.client_id||null,ticket_ref:context.ticket_ref||null,appointment_id:context.appointment_id||null,note_id:context.note_id||null,
    draft:context.draft?{customer_id:context.draft.customer_id,reference:context.draft.reference||null,text:context.draft.text.slice(0,500),revision:context.draft.revision||1}:null,
    documentation:doc?{customer_id:doc.customer_id,source_notes:doc.source_notes,corrections:doc.corrections||[],draft:doc.draft||null,revision:doc.revision||1,note_id:doc.note_id||null,appointment_id:doc.appointment_id||null}:null};
}

export const KIND_LABELS={record:'Record',client_stated:'Client said',suggestion:'AI suggestion',gap:'Gap',guidance:'Guidance'};
export const STATUS_WORDS={submitted:'Submitted',in_review:'In review',waiting_on_client:'Waiting on client',resolved:'Resolved',closed:'Closed'};
export const FORMAT_WORDS={video:'Video',phone:'Phone',in_person:'In person',unknown:'Not stated'};
export const URGENCY_ORDER=['urgent','high','normal','low'];
export function display(name) {return (name||'').replace(/\s*\(DEMO\)\s*$/,'');}

export function ageText(hours) {
  if(hours===null||hours===undefined)return '';
  if(hours<1)return 'under an hour';
  if(hours<48)return `${hours} h`;
  return `${Math.floor(hours/24)} days`;
}
// "Received 26 hours ago" / "Received 4 days ago": plain words for a ticket's age.
export function receivedText(hours) {
  if(hours===null||hours===undefined)return '';
  if(hours<1)return 'Received less than an hour ago';
  if(hours<48)return `Received ${hours} hour${hours===1?'':'s'} ago`;
  const days=Math.floor(hours/24);
  return `Received ${days} days ago`;
}

export const REPORT_TITLES={ticket_inbox:'Ticket triage',priorities:'Follow-up priorities',reviews:'Review deadlines',schedule:'Your schedule',
  client_overview:'Client overview',meeting_history:'Meeting history',meeting_brief:'Meeting briefing',ticket_summary:'Ticket summary',
  draft_message:'Client message',document_interaction:'Interaction record',actions_from_note:'Follow-up actions',handover_brief:'Handover brief',book_question:'Across your book',help:'RM assistant'};

// A reply as plain text with numbered references and a source list (for Copy).
export function reportText(reply,client) {
  if(!reply)return '';
  const sources=reply.sources||[],number=new Map(sources.map((s,i)=>[s.id,i+1]));
  const lines=[`${REPORT_TITLES[reply.intent]||'RM assistant'}${client?` – ${client}`:''}`,''];
  if(reply.summary)lines.push(reply.summary,'');
  for(const section of reply.sections||[]) {
    if(section.heading)lines.push(section.heading.toUpperCase());
    for(const item of section.items) {
      const refs=(item.source_ids||[]).filter(id=>number.has(id)).map(id=>`[${number.get(id)}]`).join('');
      lines.push(`- ${item.kind!=='record'?`${KIND_LABELS[item.kind]||item.kind}: `:''}${item.text}${refs?` ${refs}`:''}`);
    }
    lines.push('');
  }
  for(const d of allDrafts(reply))lines.push(`Draft${d.client?` to ${d.client}`:''} (not sent):`,d.text,'');
  if(sources.length)lines.push('Sources',...sources.map((s,i)=>`[${i+1}] ${s.title}`));
  lines.push('','AI-generated from DemoBank demo records. Check the sources before acting.');
  return lines.join('\n');
}

// Live search over the RM's book (names only; the server still refuses anything outside the book).
export function filterBook(book,query) {
  const q=(query||'').trim().toLowerCase();
  if(!q)return book||[];
  return (book||[]).filter(c=>c.name.toLowerCase().split(/\s+/).some(part=>part.startsWith(q))||c.name.toLowerCase().includes(q));
}

// Meetings per client per week (Mon-Sun, by the appointment date) to show where the weekly limit is reached or passed.
export function weekStart(date) {
  const d=new Date(`${date}T00:00:00Z`);const day=(d.getUTCDay()+6)%7;d.setUTCDate(d.getUTCDate()-day);return d.toISOString().slice(0,10);
}
export function meetingsPerWeek(appointments) {
  const counts=new Map();
  for(const a of appointments||[]) {const key=`${a.customer_id}|${weekStart(a.date)}`;counts.set(key,(counts.get(key)||0)+1);}
  return counts;
}
export function groupByDate(appointments) {
  const groups=[];
  for(const a of appointments||[]) {const last=groups.at(-1);if(last&&last.date===a.date)last.items.push(a);else groups.push({date:a.date,items:[a]});}
  return groups;
}

// Withheld items are counted, never shown: the RM learns that something could not be verified.
export function validationNote(validation) {
  const n=validation?.withheld||0;
  if(!n)return '';
  return `${n} point${n===1?'':'s'} could not be verified against your records and ${n===1?'was':'were'} left out.`;
}

// Follow-ups grouped for the page: overdue, next 7 days, later, specialist-managed and recorded commitments.
export function followupGroups(items,today) {
  const groups={overdue:[],week:[],later:[],specialist:[],commitments:[]};
  const limit=new Date(`${today}T00:00:00Z`);limit.setUTCDate(limit.getUTCDate()+7);
  const week=limit.toISOString().slice(0,10);
  for(const item of items||[]) {
    if(item.owner==='specialist')groups.specialist.push(item);
    else if(item.kind==='commitment')groups.commitments.push(item);
    else if(item.due_on&&item.due_on<today)groups.overdue.push(item);
    else if(item.due_on&&item.due_on<=week)groups.week.push(item);
    else groups.later.push(item);
  }
  return groups;
}

// 14-day availability as rows of half-hour cells: free, booked, held, blocked (block id kept so it can be removed).
export function dayCells(day) {
  if(!day.hours)return [];
  const toMin=t=>{const [h,m]=t.split(':').map(Number);return h*60+m;};
  const cells=[];
  for(let m=toMin(day.hours.starts);m<toMin(day.hours.ends);m+=30) {
    const time=`${String(Math.floor(m/60)).padStart(2,'0')}:${String(m%60).padStart(2,'0')}`;
    const inside=(s,e)=>toMin(s)<=m&&m<toMin(e);
    const booking=day.bookings.find(b=>inside(b.starts,b.ends));
    const block=day.blocks.find(b=>inside(b.starts,b.ends));
    const state=booking?(booking.status==='held'?'held':'booked'):block?'blocked':day.free.includes(time)?'free':'past';
    cells.push({time,state,client:booking?.client,block_id:block?.block_id,reason:block?.reason});
  }
  return cells;
}
export function sgtInstant(date,time) {return new Date(`${date}T${time}:00+08:00`).toISOString();}
export function plusMinutes(iso,minutes) {return new Date(new Date(iso).getTime()+minutes*60000).toISOString();}
export const WEEKDAY_NAMES=['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];

// RM5/RM6/RM10: an approved documentation draft becomes the interaction note; actions become requests or tasks.
export function interactionPayload(doc,edits,requestKey) {
  const items=(edits.items||doc.draft.items||[]).filter(i=>!i.removed);
  const pick=category=>items.filter(i=>i.category===category).map(i=>i.certainty==='tentative'?`${i.text} (tentative)`:i.text).slice(0,10);
  return {request_key:requestKey,customer_id:doc.customer_id,met_on:edits.met_on,format:edits.format,title:edits.title||'',summary:edits.summary,
    commitments:pick('commitment'),decisions:pick('decision'),client_requests:pick('client_request'),source_notes:doc.source_notes,
    structured:{items,gaps:doc.draft.gaps||[],actions:(doc.draft.actions||[]).map(a=>({kind:a.kind,title:a.title,origin:a.origin})),
      ...(doc.appointment_id?{appointment_id:doc.appointment_id}:{})},
    revisions:(doc.corrections||[]).map((text,index)=>({revision:index+2,correction:text,confirmed_by:'rm'}))};
}
export function taskFromAction(action) {
  return {request_key:newId(),title:(action.title||'').slice(0,200),details:(action.details&&typeof action.details==='string'?action.details:'').slice(0,500),
    owner:(action.owner||'').slice(0,80),due_on:action.due_on||null};
}
export function requestFromAction(action,customerId) {
  return {customer_id:customerId,category:action.request?.category||'',summary:(action.title||'').slice(0,500),
    details:{...(action.details&&typeof action.details==='object'?action.details:{})},urgency:'normal'};
}
export function missingFields(category,details,summary) {
  if(!category)return ['Request type'];
  const missing=category.fields.filter(f=>f.required&&!(details[f.key]||'').trim()).map(f=>f.label);
  if(!(summary||'').trim())missing.push('Short summary');
  return missing;
}

// Ticket list grouping for the inbox page.
export function ticketsByOwner(data) {
  return {mine:data?.rm_owned||[],specialist:data?.specialist||[]};
}

// Saved conversations (0091). Full source records are not stored (they are re-read when asked again), and the oldest
// turns are dropped until the saved conversation fits the API's request size.
export const SAVE_LIMIT=60000;
export function conversationTitle(messages) {
  const first=messages.find(m=>m.role==='user'&&m.content)?.content||'Conversation';
  const line=first.replace(/\s+/g,' ').trim();
  return line.length>60?`${line.slice(0,59)}…`:line;
}
export function conversationPayload(messages,context) {
  let kept=messages.filter(m=>!m.pending&&!m.error&&(m.role==='user'?m.content:m.reply)).map(m=>m.role==='user'
    ?{role:'user',content:m.content,client_id:m.client_id??null}
    :{role:'assistant',client_id:m.client_id??null,ask:m.ask||null,reply:{...m.reply,sources:(m.reply.sources||[]).map(({record,...source})=>source)}});
  const title=conversationTitle(kept);
  let saved=requestContext(context);
  const size=()=>JSON.stringify({title,messages:kept,context:saved}).length;
  if(size()>SAVE_LIMIT)saved={...saved,documentation:null};
  while(kept.length>1&&(size()>SAVE_LIMIT||kept.length>80))kept=kept.slice(kept[1]?.role==='assistant'?2:1);
  return {title,messages:kept,context:saved};
}
// A reopened conversation: earlier review cards are shown read-only (ask again for a fresh draft to approve).
export function restoreConversation(saved) {
  return {messages:(saved.messages||[]).map(m=>({...m,restored:true})),
    context:{client_id:null,ticket_ref:null,appointment_id:null,note_id:null,draft:null,documentation:null,...(saved.context||{})}};
}

// The Today page: what needs Sophie now, built from records only (no model call). Each list is short and sorted.
export function todayView({appointments=[],tickets=null,followups=[]},today) {
  const days=(date)=>Math.round((Date.parse(date)-Date.parse(today))/86400000);
  const confirmed=appointments.filter(a=>a.status==='confirmed');
  const upcoming=confirmed.filter(a=>!a.past&&days(a.date)>=0&&days(a.date)<=7);
  const toDocument=confirmed.filter(a=>a.past&&!a.note_id&&days(a.date)>=-30).sort((x,y)=>y.date.localeCompare(x.date));
  const mine=(tickets?.rm_owned||[]).slice().sort((x,y)=>(y.overdue-x.overdue)||((y.age_hours??0)-(x.age_hours??0)));
  const fresh=mine.filter(t=>(t.age_hours??99)<24);
  const soon=followups.filter(f=>f.owner!=='specialist'&&['milestone','review_deadline'].includes(f.kind)&&f.due_on&&days(f.due_on)>=0&&days(f.due_on)<=14)
    .sort((x,y)=>x.due_on.localeCompare(y.due_on));
  const overdueTasks=followups.filter(f=>f.kind==='task'&&f.overdue);
  return {upcoming,today:upcoming.filter(a=>days(a.date)===0),toDocument,requests:mine,fresh,pastTarget:mine.filter(t=>t.overdue),soon,overdueTasks};
}
