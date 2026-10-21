const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const CODE=/^[a-z][a-z0-9_]{0,39}$/;
export const CONSENT_CATEGORIES={profile:'Basic profile',agenda:'Meeting agenda',accounts:'Accounts',transactions:'Transactions',cards:'Cards',loans:'Loans',compliance:'Compliance records'};
function invalid() {return new Error('The recorded consent scope could not be verified. Refresh its status before retrying.');}
function id(value) {if(typeof value!=='string'||!UUID.test(value))throw invalid();return value;}
function timestamp(value) {
  if(typeof value!=='string'||!/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(value)||!Number.isFinite(Date.parse(value)))throw invalid();
  return value;
}
function name(value) {if(value!==null&&(typeof value!=='string'||!value.trim()||value.length>200))throw invalid();return value;}
function scope(value) {
  if(!value||typeof value.purpose!=='string'||!CODE.test(value.purpose)||!Array.isArray(value.categories)||!value.categories.length||value.categories.length>20||
    value.categories.some(item=>typeof item!=='string'||!CODE.test(item))||new Set(value.categories).size!==value.categories.length)throw invalid();
  return {purpose:value.purpose,recipient:id(value.recipient),categories:[...value.categories].sort()};
}
export function parseConsentRequestTarget(value) {
  const result=scope(value);
  if(result.purpose!=='rm_brief'||result.categories.some(item=>!Object.hasOwn(CONSENT_CATEGORIES,item))||!Number.isSafeInteger(value.duration_days)||value.duration_days<1||value.duration_days>365||value.recipient_name===null)throw invalid();
  return {...result,recipient_name:name(value.recipient_name),duration_days:value.duration_days};
}
export function parseConsentGrantTarget(value) {
  return {...scope(value),request_id:id(value.request_id),recipient_name:name(value.recipient_name),expires_at:timestamp(value.expires_at)};
}
export function parseConsentRevokeTarget(value) {
  return {...scope(value),grant_id:id(value.grant_id),recipient_name:name(value.recipient_name),expires_at:value.expires_at===null?null:timestamp(value.expires_at)};
}
export function parseConsentRequestResult(value) {
  const target=parseConsentRequestTarget(value);
  if(value.status!=='pending')throw invalid();
  return {...target,request_id:id(value.request_id),expires_at:timestamp(value.expires_at),status:'pending'};
}
export function parseConsentGrantResult(value) {
  const target=parseConsentGrantTarget(value);
  if(value.status!=='active')throw invalid();
  return {...target,grant_id:id(value.grant_id),status:'active'};
}
export function parseConsentRevokeResult(value) {
  const target=parseConsentRevokeTarget(value);
  if(value.status!=='revoked')throw invalid();
  return {...target,revoked_at:timestamp(value.revoked_at),status:'revoked'};
}
export function consentTargetMatches(target,result) {
  return Object.entries(target).every(([key,value])=>key==='expires_at'&&value!==null?Date.parse(result[key])===Date.parse(value):
    key==='categories'?JSON.stringify(result[key])===JSON.stringify(value):result[key]===value);
}
export function parseConsentRecords(value) {
  const data=value?.data;
  if(!data||!Array.isArray(data.requests)||!Array.isArray(data.grants)||!Number.isSafeInteger(data.requests_total)||data.requests_total<0||
    !Number.isSafeInteger(data.grants_total)||data.grants_total<0||data.requests.length!==Math.min(200,data.requests_total)||data.grants.length!==Math.min(200,data.grants_total))throw invalid();
  const requests=data.requests.map(item=>{
    if(!['pending','granted','declined','expired'].includes(item?.status))throw invalid();
    return {...scope(item),request_id:id(item.request_id),recipient_name:name(item.recipient_name),status:item.status,
      grant_expires_at:item.grant_expires_at===null?null:timestamp(item.grant_expires_at),case_id:item.case_id===null?null:id(item.case_id)};
  });
  const grants=data.grants.map(item=>{
    const target=parseConsentRevokeTarget(item);
    const revoked_at=item.revoked_at===null?null:timestamp(item.revoked_at);
    if(typeof item.active!=='boolean'||item.active&&revoked_at!==null)throw invalid();
    return {...target,request_id:item.request_id===null?null:id(item.request_id),granted_at:timestamp(item.granted_at),revoked_at,active:item.active};
  });
  if(new Set(requests.map(item=>item.request_id)).size!==requests.length||new Set(grants.map(item=>item.grant_id)).size!==grants.length)throw invalid();
  return {call_id:id(value.call_id),as_of:timestamp(value.as_of),requests,grants,requests_total:data.requests_total,grants_total:data.grants_total};
}
export function consentCategoryText(categories) {return categories.map(item=>CONSENT_CATEGORIES[item]||item).join(', ');}
