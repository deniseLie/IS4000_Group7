import {parseAction} from './actions.js';
import {parseAppointments} from './appointmentData.js';
import {parseAgendas} from './agendaData.js';
const API_URL=import.meta.env?.VITE_API_URL || '/api';
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function invalid() {return new Error('The relationship records could not be verified. Please retry.');}
function text(value) {if(typeof value!=='string'||!value.trim())throw invalid();return value;}
function code(value,pattern) {if(value!==null&&(typeof value!=='string'||!pattern.test(value)))throw invalid();return value;}
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
    const customer_action=item.customer_action===undefined||item.customer_action===null?null:text(item.customer_action);
    return {request_id:id(item.request_id),advisor_id:item.advisor_id===undefined||item.advisor_id===null?null:id(item.advisor_id),who:text(item.who),what:text(item.what),status:item.status,urgency:item.urgency,deadline:item.deadline,country:code(item.country,/^[A-Z]{2}$/),currency:code(item.currency,/^[A-Z]{3}$/),customer_action};
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

export async function loadAppointments(token,signal) {
  return parseAppointments(await body(await fetch(`${API_URL}/relationship/appointments`,{headers:{Authorization:`Bearer ${token}`},signal})));
}

export async function loadAgendas(token,signal) {
  return parseAgendas(await body(await fetch(`${API_URL}/relationship/agendas`,{headers:{Authorization:`Bearer ${token}`},signal})));
}

export async function holdAppointment(query,token,signal) {
  const result=parseAction(await body(await fetch(`${API_URL}/relationship/holds`,{method:'POST',
    headers:{Authorization:`Bearer ${token}`,'Content-Type':'application/json'},body:JSON.stringify(query),signal})));
  if(result.command.case_id!==query.case_id||result.command.type!=='appointment.hold')throw invalid();
  return result;
}
