const API_URL=import.meta.env?.VITE_API_URL || '/api';
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function invalid() {return new Error('The relationship records could not be verified. Please retry.');}
function text(value) {if(typeof value!=='string'||!value.trim())throw invalid();return value;}
function id(value) {if(typeof value!=='string'||!UUID.test(value))throw invalid();return value;}
function timestamp(value) {
  if(typeof value!=='string'||!/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(value)||!Number.isFinite(Date.parse(value)))throw invalid();
  return value;
}
function envelope(value) {
  if(!value||!Array.isArray(value.data))throw invalid();
  return {call_id:id(value.call_id),as_of:timestamp(value.as_of)};
}

export function parseRequests(value) {
  const metadata=envelope(value);
  const data=value.data.map(item=>{
    if(!item||!['sent','seen','answered','closed'].includes(item.status)||!['low','normal','high'].includes(item.urgency))throw invalid();
    if(item.deadline!==null&&(typeof item.deadline!=='string'||!/^\d{4}-\d\d-\d\d$/.test(item.deadline)||!Number.isFinite(Date.parse(item.deadline))))throw invalid();
    return {request_id:id(item.request_id),who:text(item.who),what:text(item.what),status:item.status,urgency:item.urgency,deadline:item.deadline};
  });
  return {...metadata,data};
}

export function parseSlots(value) {
  const metadata=envelope(value);
  const data=value.data.map(item=>{
    if(!item)throw invalid();
    const starts_at=timestamp(item.starts_at),ends_at=timestamp(item.ends_at);
    if(Date.parse(ends_at)-Date.parse(starts_at)!==30*60*1000)throw invalid();
    return {advisor_id:id(item.advisor_id),starts_at,ends_at};
  });
  return {...metadata,data};
}

async function body(response) {
  let value;
  try {value=await response.json();}
  catch(error) {if(error instanceof SyntaxError)throw new Error('The relationship response could not be verified. Please retry.',{cause:error});throw error;}
  if(!response.ok)throw new Error(typeof value?.error?.message==='string'&&value.error.message.trim()?value.error.message:'Relationship records cannot be loaded. Please retry.');
  return value;
}

export async function loadRequests(token,signal) {
  return parseRequests(await body(await fetch(`${API_URL}/relationship/requests`,{headers:{Authorization:`Bearer ${token}`},signal})));
}

export async function loadSlots(token,query,signal) {
  return parseSlots(await body(await fetch(`${API_URL}/relationship/slots`,{method:'POST',
    headers:{Authorization:`Bearer ${token}`,'Content-Type':'application/json'},body:JSON.stringify(query),signal})));
}
