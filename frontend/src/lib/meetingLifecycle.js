const API=import.meta.env?.VITE_API_URL||'/api';
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export class MeetingError extends Error {constructor(message,status){super(message);this.status=status;}}
export async function meetingRequest(path,method,value,token,signal){
  const response=await fetch(`${API}/meetings${path}`,{method,headers:{Authorization:`Bearer ${token}`,...(value===null?{}:{'Content-Type':'application/json'})},body:value===null?undefined:JSON.stringify(value),signal});
  let body;
  try{body=await response.json();}catch(failure){if(!(failure instanceof SyntaxError))throw failure;throw new MeetingError(`The meeting service returned an unreadable response (HTTP ${response.status}). Check the backend connection before retrying.`,response.status);}
  if(!response.ok){const detail=body.error?.message||body.detail?.message||body.detail;
    const message=Array.isArray(detail)?detail.map(item=>`${Array.isArray(item.loc)?item.loc.filter(part=>part!=='body').join('.'):'request'}: ${item.msg}`).join('; '):detail;
    throw new MeetingError(typeof message==='string'?message:`Meeting request failed (${response.status}).`,response.status);}

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
  return lines.map((line,index)=>{const match=line.match(/^(rm|client|unmapped):\s*(.+)$/i);if(!match)throw new Error('Start each note with rm:, client:, or unmapped:.');return {cue_id:`cue${index+1}`,speaker:match[1].toLowerCase(),start_seconds:null,text:match[2]};});
}
