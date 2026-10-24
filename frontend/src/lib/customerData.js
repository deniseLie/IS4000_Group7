const API_URL=import.meta.env?.VITE_API_URL || '/api';
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
function invalid() {return new Error('The recorded customer data could not be verified. Reload records to retry.');}
function text(value) {if(typeof value!=='string'||!value.trim()||value.length>2000)throw invalid();return value;}
function number(value) {if(typeof value!=='number'||!Number.isFinite(value))throw invalid();return value;}
function choice(value,allowed) {if(!allowed.includes(value))throw invalid();return value;}
function match(value,pattern) {if(typeof value!=='string'||!pattern.test(value))throw invalid();return value;}
function day(value) {
  match(value,/^\d{4}-\d\d-\d\d$/);
  const parsed=new Date(`${value}T00:00:00Z`);
  if(!Number.isFinite(parsed.getTime())||parsed.toISOString().slice(0,10)!==value)throw invalid();
  return value;
}
function timestamp(value) {match(value,/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/);if(!Number.isFinite(Date.parse(value)))throw invalid();return value;}
function nullable(value,parse) {return value===null?null:parse(value);}
function rows(value,parse,key) {
  if(!Array.isArray(value)||value.length>200)throw invalid();
  const result=value.map(parse);
  if(new Set(result.map(row=>row[key])).size!==result.length)throw invalid();
  return result;
}
function profile(value) {
  return {name:text(value?.name),preferred_language:text(value?.preferred_language),
    employment_status:choice(value?.employment_status,['employed','self_employed','student','retired','unemployed'])};
}
function account(value) {
  return {account_id:match(value?.account_id,/^acc_[1-9][0-9]{0,17}$/),number:match(value?.number,/^\*{4}[0-9]{4}$/),
    type:choice(value?.type,['savings','current','multi_currency']),currency:match(value?.currency,/^[A-Z]{3}$/),balance:number(value?.balance),
    status:choice(value?.status,['active','dormant','closed'])};
}
function card(value) {
  return {card_id:match(value?.card_id,/^card_[1-9][0-9]{0,17}$/),name:text(value?.name),last4:match(value?.last4,/^[0-9]{4}$/),
    status:choice(value?.status,['active','temporarily_blocked','cancelled','blocked','expired']),limit:number(value?.limit),
    outstanding:number(value?.outstanding),minimum_payment:number(value?.minimum_payment),due_date:nullable(value?.due_date,day)};
}
function loan(value) {
  return {loan_id:match(value?.loan_id,/^loan_[1-9][0-9]{0,17}$/),type:choice(value?.type,['home','car','personal','securities_backed']),
    principal:number(value?.principal),outstanding:number(value?.outstanding),next_amount:number(value?.next_amount),
    next_payment:nullable(value?.next_payment,day),status:choice(value?.status,['active','paid_off'])};
}
function transaction(value) {
  const result={txn_id:match(value?.txn_id,/^txn_[1-9][0-9]{0,17}$/),date:day(value?.date),description:text(value?.description),
    amount:number(value?.amount),currency:match(value?.currency,/^[A-Z]{3}$/),status:choice(value?.status,['posted','pending','failed']),
    card_id:nullable(value?.card_id,id=>match(id,/^card_[1-9][0-9]{0,17}$/)),card_last4:nullable(value?.card_last4,last4=>match(last4,/^[0-9]{4}$/)),
    failure_reason:nullable(value?.failure_reason,text),is_overseas:value?.is_overseas};
  if(typeof result.is_overseas!=='boolean'||(result.card_id===null)!==(result.card_last4===null))throw invalid();
  return result;
}
function review(value) {
  if(value===null)return null;
  return {case_id:match(value?.case_id,UUID),title:text(value?.title),notice_text:text(value?.notice_text),notice_date:day(value?.notice_date),
    due_date:nullable(value?.due_date,day),status:choice(value?.status,['review_opened','documents_requested','documents_partially_received',
      'awaiting_client_documents','under_review','information_requested','completed','closed']),
    requested_items:rows(value?.requested_items,item=>({item_id:match(item?.item_id,UUID),label:text(item?.label),
      instructions:nullable(item?.instructions,text),submission_status:choice(item?.submission_status,['not_submitted','submitted_awaiting_review','accepted','rejected','unknown']),
      client_mark:choice(item?.client_mark,['prepared','not_prepared']),client_marked_at:nullable(item?.client_marked_at,timestamp)}),'item_id')};
}
function advisor(value) {
  if(value===null)return null;
  return {current:nullable(value?.current,assignment=>({rm_id:match(assignment?.rm_id,UUID),name:text(assignment?.name),role:text(assignment?.role),
    team:text(assignment?.team),started_on:day(assignment?.started_on),contact:{email:nullable(assignment?.contact?.email,text),phone:nullable(assignment?.contact?.phone,text)}})),previous:nullable(value?.previous,assignment=>({
    rm_id:match(assignment?.rm_id,UUID),name:text(assignment?.name),ended_on:day(assignment?.ended_on)}))};
}
function documentRecord(value) {
  const result={document_id:match(value?.document_id,UUID),case_id:match(value?.case_id,UUID),case_title:text(value?.case_title),
    item_id:nullable(value?.item_id,id=>match(id,UUID)),item_label:nullable(value?.item_label,text),file_name:text(value?.file_name),
    mime_type:choice(value?.mime_type,['application/pdf','image/jpeg','image/png','application/vnd.openxmlformats-officedocument.wordprocessingml.document']),size_bytes:number(value?.size_bytes),
    precheck_status:choice(value?.precheck_status,['not_checked','pending','passed','issues_found']),uploaded_at:timestamp(value?.uploaded_at)};
  if(!Number.isInteger(result.size_bytes)||result.size_bytes<1||result.size_bytes>10485760||
    (result.item_id===null)!==(result.item_label===null))throw invalid();
  return result;
}
export function parseRecord(value,parse) {
  return {call_id:match(value?.call_id,UUID),as_of:timestamp(value?.as_of),data:parse(value?.data)};
}
async function body(response) {
  let value;
  try {value=await response.json();}catch(error) {if(error instanceof SyntaxError)throw invalid();throw error;}
  if(!response.ok)throw new Error(typeof value?.error?.message==='string'?value.error.message:'Recorded customer data is unavailable. Reload records to retry.');
  return value;
}
const RESOURCES={profile,accounts:value=>rows(value,account,'account_id'),cards:value=>rows(value,card,'card_id'),
  loans:value=>rows(value,loan,'loan_id'),compliance:review,advisor,documents:value=>rows(value,documentRecord,'document_id')};
// Pages read only the sections they show; each section stays a separate audited read.
export async function loadSections(token,paths,signal) {
  const headers={Authorization:`Bearer ${token}`};
  return Object.fromEntries(await Promise.all(paths.map(async path=>[path,parseRecord(await body(await fetch(`${API_URL}/customer/${path}`,{headers,signal})),RESOURCES[path])])));
}
export async function loadRecords(token,query,signal) {
  const headers={Authorization:`Bearer ${token}`};
  const results=await loadSections(token,Object.keys(RESOURCES),signal);
  const transactions=parseRecord(await body(await fetch(`${API_URL}/customer/transactions`,{method:'POST',
    headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify(query),signal})),value=>rows(value,transaction,'txn_id'));
  return {...results,transactions};
}
