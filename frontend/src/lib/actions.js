import {parseStoredUpload,parseUploadResult,uploadTargetMatches} from './documentUploadData.js';
import {parseRequestTarget,parseRequestResult,requestTargetMatches} from './requestData.js';
import {disputeTargetMatches,parseDisputeAnswers,parseDisputeResult,parseDisputeTarget} from './disputeData.js';
import {parseChecklistTarget,parseChecklistResult,checklistTargetMatches} from './checklistData.js';
import {parseWorkflow} from './workflows.js';
import {parseAppointmentResult,parseAppointmentTarget,parseSlotTarget} from './appointmentData.js';
import {agendaTargetMatches,parseAgendaResult,parseAgendaTarget,parseAgendaShareResult,parseAgendaShareTarget} from './agendaData.js';
import {consentTargetMatches,parseConsentGrantResult,parseConsentGrantTarget,parseConsentRequestResult,parseConsentRequestTarget,parseConsentRevokeResult,parseConsentRevokeTarget} from './consentData.js';
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const CARD=/^card_[1-9][0-9]{0,14}$/;
const API_URL=import.meta.env?.VITE_API_URL || '/api';
const STATES=['proposed','confirmed','succeeded','failed','expired','rejected'];
const PRIORITIES=['P0','P1','P2','P3'];
const STAFF=['card.unblock','card.cancel_permanently','handoff.claim','handoff.close'];
const APPOINTMENTS=['appointment.hold','appointment.confirm','appointment.cancel'];
const CONSENTS=['consent.request','consent.grant','consent.revoke'];

function invalid() {return new Error('The recorded action could not be verified. Reload its status before retrying.');}
function id(value) {if(typeof value!=='string'||!UUID.test(value))throw invalid();return value;}
function timestamp(value) {
  if(typeof value!=='string'||!/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(value)||!Number.isFinite(Date.parse(value)))throw invalid();
  return value;
}

export function parseAction(value) {
  const command=value?.command,receipt=value?.receipt;
  if(!command||!['card.temporary_block','card.dispute','handoff.create','agenda.save','rm.share_summary','compliance.update_checklist_item','requests.create','document.register','document.submit',...STAFF,...APPOINTMENTS,...CONSENTS].includes(command.type)||!STATES.includes(command.state)||!Number.isSafeInteger(command.customer_id)||command.customer_id<=0)throw invalid();
  const command_id=id(command.command_id),case_id=id(command.case_id);
  if(typeof command.idempotency_key!=='string'||!command.idempotency_key.startsWith(`${case_id}:`)||!command.idempotency_key.endsWith(`:${command.type}`)||!/^[A-Za-z0-9_-]{1,40}$/.test(command.idempotency_key.split(':')[1])||command.idempotency_key.split(':').length!==3)throw invalid();
  const proposed_at=timestamp(command.proposed_at),expires_at=timestamp(command.expires_at);
  const confirmed_at=command.confirmed_at===null?null:timestamp(command.confirmed_at);
  if(Date.parse(expires_at)<=Date.parse(proposed_at))throw invalid();
  if(STAFF.includes(command.type)&&(!['succeeded','failed'].includes(command.state)||confirmed_at===null))throw invalid();
  // Only a decided dispute records the customer's answer about the card.
  const answers=command.confirmation_input===null||command.confirmation_input===undefined?null:parseDisputeAnswers(command.confirmation_input);
  if(answers&&(command.type!=='card.dispute'||command.state==='proposed'))throw invalid();
  const target=command.target;
  let canonical;
  if(['card.temporary_block','card.unblock','card.cancel_permanently'].includes(command.type)) {
    if(!target||typeof target.card_id!=='string'||!CARD.test(target.card_id)||typeof target.last4!=='string'||!/^\d{4}$/.test(target.last4))throw invalid();
    canonical={card_id:target.card_id,last4:target.last4};
    if(STAFF.includes(command.type))canonical={...canonical,handoff_id:id(target.handoff_id)};
  } else if(command.type==='handoff.create') {
    if(!target||!['card_verification','card_dispute','general_support'].includes(target.reason)||!PRIORITIES.includes(target.priority)||typeof target.summary!=='string'||!target.summary.trim()||target.summary.length>1000)throw invalid();
    canonical={reason:target.reason,priority:target.priority,summary:target.summary};
  } else if(command.type==='card.dispute')canonical=parseDisputeTarget(target);
  else if(command.type==='appointment.hold')canonical=parseSlotTarget(target);
  else if(APPOINTMENTS.includes(command.type))canonical=parseAppointmentTarget(target);
  else if(command.type==='consent.request')canonical=parseConsentRequestTarget(target);
  else if(command.type==='consent.grant')canonical=parseConsentGrantTarget(target);
  else if(command.type==='consent.revoke')canonical=parseConsentRevokeTarget(target);
  else if(command.type==='compliance.update_checklist_item')canonical=parseChecklistTarget(target);
  else if(command.type==='requests.create')canonical=parseRequestTarget(target);
  else if(command.type==='document.register')canonical=parseStoredUpload(target,command.customer_id);
  else if(command.type==='document.submit') {
    if(typeof target?.item_label!=='string'||!target.item_label.trim()||typeof target.file_name!=='string'||!target.file_name.trim()||typeof target.sha256!=='string'||!/^[a-f0-9]{64}$/.test(target.sha256))throw invalid();
    canonical={document_id:id(target.document_id),check_id:id(target.check_id),case_id:id(target.case_id),item_id:id(target.item_id),item_label:target.item_label,file_name:target.file_name,sha256:target.sha256};
  }
  else if(command.type==='agenda.save')canonical=parseAgendaTarget(target);
  else if(command.type==='rm.share_summary')canonical=parseAgendaShareTarget(target);
  else canonical={handoff_id:id(target?.handoff_id)};
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
    } else if(command.type==='card.dispute') {
      result=parseDisputeResult(receipt.result);
      if(confirmed_at===null||!answers||result.card_in_possession!==answers.card_in_possession||!disputeTargetMatches(canonical,result))throw invalid();
    } else if(command.type==='handoff.create') {
      if(!receipt.result||!PRIORITIES.includes(receipt.result.priority)||!['open','claimed'].includes(receipt.result.status)||typeof receipt.result.created!=='boolean')throw invalid();
      result={handoff_id:id(receipt.result.handoff_id),priority:receipt.result.priority,status:receipt.result.status,created:receipt.result.created};
    } else if(['card.unblock','card.cancel_permanently'].includes(command.type)) {
      const status=command.type==='card.unblock'?'active':'cancelled';
      if(receipt.result?.card_id!==canonical.card_id||receipt.result?.status!==status)throw invalid();
      result={card_id:canonical.card_id,status};
    } else if(APPOINTMENTS.includes(command.type)) {
      result=parseAppointmentResult(receipt.result);
      const status={'appointment.hold':'held','appointment.confirm':'confirmed','appointment.cancel':'cancelled'}[command.type];
      if(result.status!==status||command.type!=='appointment.hold'&&confirmed_at===null||Object.entries(canonical).some(([key,value])=>
        ['starts_at','ends_at','hold_expires_at'].includes(key)&&value!==null?Date.parse(result[key])!==Date.parse(value):result[key]!==value))throw invalid();
    } else if(command.type==='compliance.update_checklist_item') {
      result=parseChecklistResult(receipt.result);
      if(!checklistTargetMatches(canonical,result))throw invalid();
    } else if(command.type==='requests.create') {
      result=parseRequestResult(receipt.result);
      if(confirmed_at===null||!requestTargetMatches(canonical,result))throw invalid();
    } else if(command.type==='document.submit') {
      if(receipt.result?.status!=='submitted_awaiting_review'||Object.entries(canonical).some(([key,value])=>receipt.result[key]!==value))throw invalid();
      result={...canonical,status:'submitted_awaiting_review'};
    } else if(command.type==='document.register') {
      result=parseUploadResult(receipt.result,command.customer_id);
      if(!uploadTargetMatches(canonical,result))throw invalid();
    } else if(command.type==='agenda.save') {
      result=parseAgendaResult(receipt.result);
      if(!agendaTargetMatches(canonical,result))throw invalid();
    } else if(command.type==='rm.share_summary') {
      result=parseAgendaShareResult(receipt.result);
      if(confirmed_at===null||Date.parse(result.shared_at)<Date.parse(confirmed_at)||!agendaTargetMatches(canonical,result))throw invalid();
    } else if(CONSENTS.includes(command.type)) {
      if(command.type==='consent.request')result=parseConsentRequestResult(receipt.result);
      else if(command.type==='consent.grant')result=parseConsentGrantResult(receipt.result);
      else result=parseConsentRevokeResult(receipt.result);
      if(!consentTargetMatches(canonical,result)||command.type!=='consent.request'&&confirmed_at===null)throw invalid();
    } else {
      const status=command.type==='handoff.claim'?'claimed':'closed';
      if(receipt.result?.handoff_id!==canonical.handoff_id||receipt.result?.status!==status)throw invalid();
      result={handoff_id:canonical.handoff_id,status};
    }
    saved={receipt_id:receipt.receipt_id,command_id,outcome:receipt.outcome,result,audit_event_id:receipt.audit_event_id,created_at:timestamp(receipt.created_at)};
  }
  const workflow=value.workflow===null||value.workflow===undefined?null:parseWorkflow(value.workflow);
  if(workflow&&(workflow.case_id!==case_id||workflow.customer_id!==command.customer_id))throw invalid();
  return {command:{command_id,case_id,type:command.type,target:canonical,state:command.state,proposed_at,expires_at,confirmed_at,answers},receipt:saved,workflow};
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
export async function loadAction(commandId,token,signal) {
  const saved=parseAction(await responseBody(await fetch(`${API_URL}/commands/${id(commandId)}`,{headers:{Authorization:`Bearer ${token}`},signal})));
  if(saved.command.command_id!==commandId)throw invalid();
  return saved;
}
// A dispute draft is confirmed with the customer's answer about the card; other actions send no body.
export async function confirmAction(commandId,token,signal,answers=null) {
  const response=await fetch(`${API_URL}/commands/${id(commandId)}/confirm`,{method:'POST',signal,
    headers:answers?{Authorization:`Bearer ${token}`,'Content-Type':'application/json'}:{Authorization:`Bearer ${token}`},
    ...(answers?{body:JSON.stringify({answers:parseDisputeAnswers(answers)})}:{})});
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
export async function proposeAction(query,token,signal) {
  const result=parseAction(await responseBody(await fetch(`${API_URL}/commands`,{method:'POST',
    headers:{Authorization:`Bearer ${token}`,'Content-Type':'application/json'},body:JSON.stringify(query),signal})));
  if(result.command.case_id!==query.case_id||result.command.type!==query.type)throw invalid();
  return result;
}
export async function createActionCase(query,token,signal) {
  const result=await responseBody(await fetch(`${API_URL}/actions/cases`,{method:'POST',
    headers:{Authorization:`Bearer ${token}`,'Content-Type':'application/json'},body:JSON.stringify(query),signal}));
  if(result?.case_id!==query.case_id||result?.session_id!==query.session_id||!Number.isSafeInteger(result?.customer_id)||result.customer_id<=0)throw invalid();
  return {case_id:id(result.case_id),session_id:id(result.session_id),customer_id:result.customer_id};
}
