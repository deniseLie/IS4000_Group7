import {TOPICS} from './meetingRequests.js';
const API=import.meta.env?.VITE_API_URL||'/api';
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export class MeetingError extends Error {constructor(message,status){super(message);this.status=status;}}
// Plain-language versions of the meeting service's technical errors, each with what to do next.
const FRIENDLY=[
  [/transcript position exceeds/i,'This transcript runs longer than the recorded call. Upload it again (it is now fitted to the call length automatically), or correct the call times under "Review call times".'],
  [/due date needs exact ISO-date/i,'A follow-up date in the draft could not be matched to your notes. Click Draft minutes again; any unmatched date is left blank for you to set when you review.'],
  [/action owner must match|conditional action needs|conditional or tentative wording|only conditional actions/i,'A follow-up in the draft did not match who said it or how firm it was. Click Draft minutes again; you can adjust owners and dates when you review.'],
  [/recording permission, notice, occurrence or participant roster/i,'The recording consent or meeting details changed since this page loaded. Refresh the page, then try again.'],
  [/enter notes for the current completed call occurrence|not current/i,'This page is out of date or the call is not marked as ended yet. Refresh the page and try again.'],
  [/1-100 bounded redacted cues|Enter between 1 and 100/i,'Enter between 1 and 100 note lines, and keep the notes under about 15,000 characters.'],
  [/1-1000 redacted cues|128000/i,'This transcript is too long to import. Upload a shorter export or use typed notes instead.'],
  [/must be redacted|invalid source cue identity, text or speaker/i,'One line could not be saved. Check that every line has text and does not include a card number, ID or password.'],
  [/generated_draft_invalid|could not be verified/i,'The AI draft did not pass our checks, so nothing was saved. Click Draft minutes again.'],
  [/timed? ?out|timeout|Schema is too complex|HTTP 50[234]/i,'The AI took too long to respond, so nothing was saved. Wait a few seconds and try again.'],
];
export function friendlyMeetingError(message){const found=FRIENDLY.find(([pattern])=>pattern.test(String(message||'')));return found?found[1]:message;}
export async function meetingRequest(path,method,value,token,signal){
  const response=await fetch(`${API}/meetings${path}`,{method,headers:{Authorization:`Bearer ${token}`,...(value===null?{}:{'Content-Type':'application/json'})},body:value===null?undefined:JSON.stringify(value),signal});
  let body;
  try{body=await response.json();}catch(failure){if(!(failure instanceof SyntaxError))throw failure;throw new MeetingError(`The meeting service returned an unreadable response (HTTP ${response.status}). Check the backend connection before retrying.`,response.status);}
  if(!response.ok){const detail=body.error?.message||body.detail?.message||body.detail;
    const message=Array.isArray(detail)?detail.map(item=>`${Array.isArray(item.loc)?item.loc.filter(part=>part!=='body').join('.'):'request'}: ${item.msg}`).join('; '):detail;
    throw new MeetingError(friendlyMeetingError(typeof message==='string'?message:`Meeting request failed (HTTP ${response.status}).`),response.status);}

  return body;
}
export async function downloadMeetingPdf(path,filename,token,signal){
  const response=await fetch(`${API}/meetings${path}`,{headers:{Authorization:`Bearer ${token}`,Accept:'application/pdf'},signal});
  if(!response.ok){const body=await response.json();throw new MeetingError(body.error?.message||body.detail?.message||`PDF download failed (HTTP ${response.status}).`,response.status);}
  if(!response.headers.get('Content-Type')?.startsWith('application/pdf'))throw new MeetingError('The meeting service returned an unexpected document format.',response.status);
  const data=await response.blob();
  if(await data.slice(0,5).text()!=='%PDF-')throw new MeetingError('The meeting service returned an invalid PDF document.',response.status);
  const url=URL.createObjectURL(data);const link=document.createElement('a');link.href=url;link.download=filename;link.click();URL.revokeObjectURL(url);
}
export function checkedWorkspace(value){
  if(!value||!UUID.test(value.meeting_id)||!Number.isInteger(value.revision)||!Number.isInteger(value.consent_epoch)||!['scheduled','in_progress','ended','cancelled'].includes(value.state)||!Array.isArray(value.followups)||typeof value.notice!=='string')throw new Error('The meeting workspace response could not be verified.');
  for(const item of value.followups)if(!UUID.test(item.followup_id)||!['rm','client'].includes(item.owner)||!['open','done'].includes(item.status)||typeof item.text!=='string')throw new Error('The meeting follow-up response could not be verified.');
  const d=value.draft;
  if(d&&(!UUID.test(d.draft_id)||!Number.isInteger(d.version)||!Number.isInteger(d.revision)||!Number.isInteger(d.consent_epoch)||!/^[a-f0-9]{64}$/.test(d.content_hash)||!d.content||!Array.isArray(d.content.actions)||!Array.isArray(d.content.summary)||!Array.isArray(d.content.decisions)))throw new Error('The minutes draft response could not be verified.');
  if(d)for(const a of d.content.actions)if(!UUID.test(a.action_id)||typeof a.quote!=='string'||!['rm','client','unassigned'].includes(a.owner)||!['agreed','conditional','tentative','unclear'].includes(a.commitment))throw new Error('The minutes action response could not be verified.');
  return value;
}
export function approvalFor(draft,command){return {draft_id:draft.draft_id,version:draft.version,content_hash:draft.content_hash,revision:draft.revision,consent_epoch:draft.consent_epoch,command_id:command};}
export function notesCues(text){
  const lines=text.split('\n').map(line=>line.trim()).filter(Boolean);
  if(!lines.length||lines.length>100)throw new Error('Enter between 1 and 100 note statements.');
  // Lenient for typed notes: bullets and numbers are ignored, common labels (RM, Client, Me, the demo names) map to a
  // speaker, and a line with no label is the RM's own note. The SQL still checks every cue.
  const ALIAS={rm:'rm',me:'rm',i:'rm',advisor:'rm',adviser:'rm',sophie:'rm','sophie lim':'rm',client:'client',customer:'client',michelle:'client','michelle ng':'client',unmapped:'unmapped',other:'unmapped'};
  return lines.map((raw,index)=>{const line=raw.replace(/^(?:[-*•]|\d+[.)])\s+/,'');const match=line.match(/^([A-Za-z][A-Za-z ]{0,20}?)\s*:\s*(.+)$/);
    const speaker=match&&ALIAS[match[1].trim().toLowerCase()];const text=(speaker?match[2]:line).trim();
    if(!text)throw new Error('A note line is empty.');return {cue_id:`cue${index+1}`,speaker:speaker||'rm',start_seconds:null,text};});
}

// The meeting page shows one meeting as four steps. The lifecycle stage (0132) decides which step is current; the RM
// still approves everything inside each step (brief, minutes, follow-ups, recap).
export const MEETING_STEPS=[['prepare','Prepare'],['meet','Meet'],['document','Document'],['follow_up','Follow up']];
export function meetingStep(stage,workspace){
  if(stage==='done')return MEETING_STEPS.length;
  if(stage==='following_up')return 3;
  if(stage==='awaiting_transcript'||stage==='minutes_review')return 2;
  if(workspace&&workspace.state!=='scheduled'&&workspace.state!=='cancelled')return 2;
  if(stage==='brief_approved')return 1;
  return 0;
}
export function stepState(index,current){return index<current?'done':index===current?'current':'todo';}

// Speaker names in a TXT/VTT export, read the same way as the backend (transcripts.labelled): "<v Name>text" or
// "Name: text". The RM picks each name's role from a list instead of retyping exact labels.
export function transcriptSpeakers(text){
  const names=[];
  for(const raw of String(text).replace(/\r\n?/g,'\n').split('\n')){
    const line=raw.trim();if(!line||line.includes('-->')||/^(WEBVTT|NOTE)\b/.test(line)||/^\d+$/.test(line))continue;
    const voice=line.match(/^<v(?:\.[\w.-]+)?\s+([^>]{1,80})>/),plain=voice?null:line.match(/^([^:<>]{1,80}):\s+\S/);
    const name=(voice?.[1]||plain?.[1]||'').trim();
    if(name&&!names.some(n=>n.toLowerCase()===name.toLowerCase()))names.push(name);
    if(names.length>=10)break;
  }
  return names;
}
const words=value=>String(value||'').toLowerCase().split(/[^a-z]+/).filter(w=>w.length>1);
// A first guess for each name: shares a word with the client's name → client; otherwise the first other name → RM.
export function guessRoles(names,client){
  const clientWords=new Set(words(client));let rm=false;
  return Object.fromEntries(names.map(name=>{
    if(words(name).some(w=>clientWords.has(w)))return [name,'client'];
    if(!rm){rm=true;return [name,'rm'];}
    return [name,'unmapped'];
  }));
}
export function speakerList(roles){return Object.entries(roles).filter(([,role])=>role==='rm'||role==='client').map(([label,role])=>({label,role}));}

// Where a cited brief source comes from, in the RM's words (mirrors services/meetings/assistant.TITLES).
const SOURCE_TITLES={rm_client_profile:'Client record',rm_review_status:'Compliance case',rm_tickets:'Requests',rm_meeting_notes:'Meeting notes',
  rm_transaction_insights:'Shared transaction insights',rm_talking_points:'Chat (topics she shared)',meeting_followups:'Follow-ups and tasks',rm_collateral_alert:'Collateral alert'};
export function sourceTitle(reference){const reader=String(reference).split(':')[0];return SOURCE_TITLES[reader]||reader.replaceAll('_',' ');}

// The approved minutes as plain notes for the assistant's "Document this meeting" (specialist and compliance hand-offs).
export function minutesAsNotes(draft){
  const c=draft?.content;if(!c)return '';
  const lines=[...c.summary,...c.decisions].map(item=>item.text);
  for(const a of c.actions)lines.push(`${a.owner==='rm'?'RM':a.owner==='client'?'Client':'Unassigned'} action: ${a.text}${a.due_on?` (due ${a.due_on})`:''}`);
  return lines.join('\n');
}

// What the chosen file holds, for the file card and the preview: statement count, first and last time, first lines.
export function transcriptSummary(text,limit=4){
  const cues=[];let first=null,last=null;
  for(const raw of String(text).replace(/\r\n?/g,'\n').split('\n')){
    const line=raw.trim();
    const time=line.match(/^(\d{2}:)?(\d{2}):(\d{2})[.,]\d{3}\s+-->\s+(\d{2}:)?(\d{2}):(\d{2})/);
    if(time){const start=`${time[1]||''}${time[2]}:${time[3]}`.replace(/^00:(?=\d\d:)/,''),end=`${time[4]||''}${time[5]}:${time[6]}`.replace(/^00:(?=\d\d:)/,'');first??=start;last=end;continue;}
    if(!line||/^(WEBVTT|NOTE)\b/.test(line)||/^\d+$/.test(line))continue;
    const voice=line.match(/^<v(?:\.[\w.-]+)?\s+([^>]{1,80})>(.*?)(?:<\/v>)?$/),plain=voice?null:line.match(/^([^:<>]{1,80}):\s+(.+)$/);
    cues.push({name:(voice?.[1]||plain?.[1]||'').trim(),text:(voice?.[2]||plain?.[2]||line).trim()});
  }
  return {lines:cues.length,first,last,preview:cues.slice(0,limit)};
}

// Shared by the RM pages and the meeting page (also loaded for clients, so it stays out of lib/rm.js).
export function topicName(code){return TOPICS[code]||String(code||'').replace(/_/g,' ');}
// Record text from SQL keeps codes such as documents_requested or self_employed; show them as words.
const LANGUAGES={en:'English',zh:'Chinese',ms:'Malay',ta:'Tamil'};
export function humanize(text) {
  return String(text||'')
    .replace(/Preferred language: ([a-z]{2})\b/g,(m,c)=>`Preferred language: ${LANGUAGES[c]||c}`)
    .replace(/^Recorded date: /,'')
    .replace(/ Referenced record: /,' Based on: ')
    .replace(/\b([a-z]+(?:_[a-z]+)+)\b/g,code=>code.replace(/_/g,' '));
}
// A meeting's short name: its purpose when short, else its topics (a long purpose is usually the client's own words).
export function meetingTitle(purpose,topics) {
  const p=String(purpose||'').trim();
  if(p&&p.length<=60)return p;
  const t=(topics||[]).map(topicName).join(' and ');
  return t?`${t} meeting`:p?`${p.slice(0,57)}…`:'Meeting';
}
export function clientWords(purpose) {const p=String(purpose||'').trim();return p.length>60?p:'';}
// The lifecycle stage in plain words.
const STAGE_WORDS={preparing:'Preparing the brief',brief_review:'Brief waiting for your review',brief_approved:'Ready for the meeting',
  awaiting_transcript:'Waiting for the transcript or your notes',minutes_review:'Minutes waiting for your review',following_up:'Follow-ups to send or complete',
  done:'All done',cancelled:'Cancelled',failed:'Something failed: see Technical details'};
export function stageWords(stage,waiting) {
  if(stage==='awaiting_transcript'&&waiting==='provider')return 'Waiting for the Zoom transcript';
  return STAGE_WORDS[stage]||String(stage||'').replace(/_/g,' ');
}
