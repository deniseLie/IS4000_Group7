import {parseAction} from './actions.js';
const API=import.meta.env?.VITE_API_URL||'/api';
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const CODES=['missing_field','page_count','date_invalid','date_outdated','unassessable','rules_unavailable','provider_unavailable','missing_pages'];
function invalid(){return new Error('The compliance workspace could not be verified. Reload before continuing.');}
export function identifier(value){if(typeof value!=='string'||!UUID.test(value))throw invalid();return value;}
function timestamp(value){if(typeof value!=='string'||!Number.isFinite(Date.parse(value)))throw invalid();return value;}
function text(value){if(typeof value!=='string'||!value.trim()||value.length>2000)throw invalid();return value;}
export function parseCheck(value){
 if(!value||!['no_issues','issues_found','unable_to_assess'].includes(value.status)||!['synthetic_template_only','notice_based'].includes(value.scope)||value.rule_version!==1||
  !/^[a-f0-9]{64}$/.test(value.sha256)||!Array.isArray(value.findings)||value.findings.length>12||
  !['text','ocr','unavailable'].includes(value.extraction)||!Number.isSafeInteger(value.customer_id)||value.customer_id<=0||
  value.page_count!==null&&(!Number.isSafeInteger(value.page_count)||value.page_count<1||value.page_count>20))throw invalid();
 if(value.scope==='notice_based'&&!/^[a-f0-9]{64}$/.test(value.requirements_hash))throw invalid();
 if((value.status==='no_issues')!==(value.findings.length===0))throw invalid();
 for(const item of value.findings)if(!CODES.includes(item.code)||item.field!==null&&typeof item.field!=='string')throw invalid();
 for(const key of ['check_id','document_id','case_id','item_id'])identifier(value[key]);
 timestamp(value.checked_at);if(value.expires_at)timestamp(value.expires_at);text(value.rule_id);return value;
}
export function parseWorkspace(value){
 const data=value?.data;
 identifier(value?.call_id);timestamp(value?.as_of);
 if(!data||!Array.isArray(data.documents)||data.documents.length>200||!Array.isArray(data.checks)||data.checks.length>200||
  !data.rules||typeof data.rules!=='object'||!data.current_documents||typeof data.current_documents!=='object')throw invalid();
 const policy=data.policy;
 if(!policy||!Number.isSafeInteger(policy.validity_hours)||policy.validity_hours<1||policy.validity_hours>168||!Number.isSafeInteger(policy.status_stale_days)||policy.status_stale_days<1||!['completed_months','rolling_months'].includes(policy.statement_period_mode))throw invalid();
 const review=data.review;
 if(review!==null){
  identifier(review.case_id);timestamp(review.status_updated_at);text(review.title);text(review.notice_text);
  if(!['review_opened','documents_requested','documents_partially_received','awaiting_client_documents','under_review','information_requested','completed','closed'].includes(review.status)||
   !Array.isArray(review.requested_items)||review.requested_items.length>200||!Array.isArray(review.info_requests)||review.info_requests.length>200)throw invalid();
  for(const item of review.requested_items){identifier(item.item_id);text(item.label);
   if(!['prepared','not_prepared'].includes(item.client_mark)||!['not_submitted','submitted_awaiting_review','accepted','rejected','unknown'].includes(item.submission_status))throw invalid();}
 }
 const ids=new Set(review?.requested_items.map(item=>item.item_id)||[]);
 if(ids.size!==(review?.requested_items.length||0))throw invalid();
 for(const [item,rule] of Object.entries(data.rules)){
  if(!ids.has(item)||rule.version!==1||!Array.isArray(rule.fields)||!rule.fields.length||rule.fields.length>10||!Number.isSafeInteger(rule.expected_pages)||rule.expected_pages<1||rule.expected_pages>20)throw invalid();
  text(rule.rule_id);text(rule.title);text(rule.scope);rule.fields.forEach(text);
 }
 for(const [item,doc] of Object.entries(data.current_documents)){if(!ids.has(item))throw invalid();identifier(doc);}
 for(const doc of data.documents){identifier(doc.document_id);identifier(doc.case_id);text(doc.file_name);}
 if(new Set(data.documents.map(doc=>doc.document_id)).size!==data.documents.length)throw invalid();
 for(const report of data.checks){parseCheck(report);if(report.case_id!==review?.case_id||!ids.has(report.item_id)||data.current_documents[report.item_id]!==report.document_id)throw invalid();}
 return {...data,as_of:value.as_of};
}
export function checkIsFresh(report,now=Date.now()){
 const checked=Date.parse(report.checked_at),current=Number(now);
 if(report.expires_at)return Number.isFinite(checked)&&Number.isFinite(current)&&checked<=current&&current<Date.parse(report.expires_at);
 const day=value=>new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Singapore',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(value));
 return Number.isFinite(checked)&&Number.isFinite(current)&&checked<=current&&day(checked)===day(current);
}
export function primaryStatus(item){
 if(item.submission_status==='accepted')return 'Accepted by bank';
 if(item.submission_status==='submitted_awaiting_review')return 'Submitted – awaiting review';
 if(item.submission_status==='unknown')return 'Unknown';
 if(item.submission_status==='rejected')return 'Correction requested by bank';
 return item.client_mark==='prepared'?'Prepared':'Not prepared';
}
export function findingText(finding,rule){
 if(finding.code==='missing_pages')return `The printed page numbering indicates missing page(s): ${finding.field}. Upload the complete file.`;
 if(finding.code==='missing_field')return `The notice requires ${finding.field}. I could not find clear completed evidence; add it or upload the correct document.`;
 if(finding.code==='page_count')return 'The page count does not match the explicit page requirement in your notice. Upload the complete document.';
 if(finding.code==='date_invalid')return 'The document issue date is missing, ambiguous, conflicting or later than today. Upload a copy with a clear issue date.';
 if(finding.code==='date_outdated')return `The date or period coverage does not meet this recorded requirement: “${finding.field}”. Upload the required date/periods.`;
 if(finding.code==='provider_unavailable')return 'The document interpretation provider is unavailable. Your upload is saved, but no pass or fail was given. Try checking again later.';
 return 'I cannot check this reliably. Upload a clearer copy or contact support. No pass or fail is given.';
}
async function request(path,token,signal,method='GET'){
 const response=await fetch(`${API}${path}`,{method,headers:{Authorization:`Bearer ${token}`},signal});
 const body=await response.json();if(!response.ok)throw new Error(body?.error?.message||'The compliance request failed. Reload its recorded status.');return body;
}
export async function loadWorkspace(token,signal){return parseWorkspace(await request('/documents/workspace',token,signal));}
export async function runPrecheck(documentId,token,signal,recheck=false){return parseCheck((await request(`/documents/${identifier(documentId)}/precheck?recheck=${recheck}`,token,signal,'POST')).data);}
export async function submitDocument(documentId,token,signal){
 const result=parseAction(await request(`/documents/${identifier(documentId)}/submit`,token,signal,'POST'));
 if(result.command.type!=='document.submit'||result.command.target.document_id!==documentId)throw invalid();return result;
}

export async function prepareComplianceSupport(selection,token){
 const body={session_id:crypto.randomUUID(),case_id:crypto.randomUUID(),...selection};
 const response=await fetch(`${API}/documents/support`,{method:'POST',headers:{Authorization:`Bearer ${token}`,'Content-Type':'application/json'},body:JSON.stringify(body)});
 const result=await response.json();if(!response.ok)throw new Error(result?.error?.message||'Support context could not be prepared.');
 if(result.data?.case_id!==body.case_id||typeof result.data.summary!=='string'||!result.data.summary)throw invalid();
 return result.data;
}
