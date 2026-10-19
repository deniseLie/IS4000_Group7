import {parseWorkflow} from './workflows.js';
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const CARD=/^card_[1-9][0-9]{0,14}$/;
const API_URL=import.meta.env?.VITE_API_URL || '/api';
const STATES=['proposed','confirmed','succeeded','failed','expired','rejected'];
const PRIORITIES=['P0','P1','P2','P3'];

function invalid() {return new Error('The recorded action could not be verified. Reload its status before retrying.');}
function id(value) {if(typeof value!=='string'||!UUID.test(value))throw invalid();return value;}
function timestamp(value) {
  if(typeof value!=='string'||!/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(value)||!Number.isFinite(Date.parse(value)))throw invalid();
  return value;
}

export function parseAction(value) {
  const command=value?.command,receipt=value?.receipt;
  if(!command||!['card.temporary_block','handoff.create'].includes(command.type)||!STATES.includes(command.state)||!Number.isSafeInteger(command.customer_id)||command.customer_id<=0)throw invalid();
  const command_id=id(command.command_id),case_id=id(command.case_id);
  if(typeof command.idempotency_key!=='string'||!command.idempotency_key.startsWith(`${case_id}:`)||!command.idempotency_key.endsWith(`:${command.type}`)||!/^[A-Za-z0-9_-]{1,40}$/.test(command.idempotency_key.split(':')[1])||command.idempotency_key.split(':').length!==3)throw invalid();
  const proposed_at=timestamp(command.proposed_at),expires_at=timestamp(command.expires_at);
  const confirmed_at=command.confirmed_at===null?null:timestamp(command.confirmed_at);
  if(Date.parse(expires_at)<=Date.parse(proposed_at))throw invalid();
  const target=command.target;
  let canonical;
  if(command.type==='card.temporary_block') {
    if(!target||typeof target.card_id!=='string'||!CARD.test(target.card_id)||typeof target.last4!=='string'||!/^\d{4}$/.test(target.last4))throw invalid();
    canonical={card_id:target.card_id,last4:target.last4};
  } else {
    if(!target||!['card_verification','general_support'].includes(target.reason)||!PRIORITIES.includes(target.priority)||typeof target.summary!=='string'||!target.summary.trim()||target.summary.length>1000)throw invalid();
    canonical={reason:target.reason,priority:target.priority,summary:target.summary};
  }
  if(['succeeded','failed'].includes(command.state)!==(receipt!==null&&receipt!==undefined))throw invalid();
  let saved=null;
  if(receipt!==null) {
    if(!receipt||receipt.command_id!==command_id||receipt.customer_id!==command.customer_id||receipt.outcome!==command.state||receipt.receipt_id!==`rcpt_${command_id.replaceAll('-','')}`||!Number.isSafeInteger(receipt.audit_event_id)||receipt.audit_event_id<=0)throw invalid();
    let result;
    if(command.state==='failed') {
      if(receipt.result?.error_code!=='target_changed')throw invalid();
      result={error_code:'target_changed'};
    } else if(command.type==='card.temporary_block') {
      if(confirmed_at===null||receipt.result?.card_id!==canonical.card_id||receipt.result?.status!=='temporarily_blocked')throw invalid();
      result={card_id:canonical.card_id,status:'temporarily_blocked'};
    } else {
      if(!receipt.result||!PRIORITIES.includes(receipt.result.priority)||!['open','claimed'].includes(receipt.result.status)||typeof receipt.result.created!=='boolean')throw invalid();
      result={handoff_id:id(receipt.result.handoff_id),priority:receipt.result.priority,status:receipt.result.status,created:receipt.result.created};
    }
    saved={receipt_id:receipt.receipt_id,command_id,outcome:receipt.outcome,result,audit_event_id:receipt.audit_event_id,created_at:timestamp(receipt.created_at)};
  }
  const workflow=value.workflow===null||value.workflow===undefined?null:parseWorkflow(value.workflow);
  if(workflow&&(workflow.case_id!==case_id||workflow.customer_id!==command.customer_id))throw invalid();
  return {command:{command_id,case_id,type:command.type,target:canonical,state:command.state,proposed_at,expires_at,confirmed_at},receipt:saved,workflow};
}

export function parseActions(value) {
  if(!Array.isArray(value)||value.length>50)throw invalid();
  const actions=value.map(parseAction);
  if(new Set(actions.map(item=>item.command.command_id)).size!==actions.length)throw invalid();
  return actions;
}

async function responseBody(response) {
  let body;
  try {body=await response.json();}
  catch(error) {if(error instanceof SyntaxError)throw invalid();throw error;}
  if(!response.ok)throw new Error(typeof body?.error?.message==='string'&&body.error.message.trim()?body.error.message:'The action status is unavailable. Reload it before retrying.');
  return body;
}

export async function loadActions(token,signal) {
  const response=await fetch(`${API_URL}/commands`,{headers:{Authorization:`Bearer ${token}`},signal});
  return parseActions(await responseBody(response));
}
export async function confirmAction(commandId,token,signal) {
  const response=await fetch(`${API_URL}/commands/${id(commandId)}/confirm`,{method:'POST',headers:{Authorization:`Bearer ${token}`},signal});
  const saved=parseAction(await responseBody(response));
  if(saved.command.command_id!==commandId)throw invalid();
  return saved;
}
export async function rejectAction(commandId,token,signal) {
  const response=await fetch(`${API_URL}/commands/${id(commandId)}/reject`,{method:'POST',headers:{Authorization:`Bearer ${token}`},signal});
  const saved=parseAction(await responseBody(response));
  if(saved.command.command_id!==commandId)throw invalid();
  return saved;
}
