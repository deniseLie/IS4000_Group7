const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const API_URL=import.meta.env?.VITE_API_URL || '/api';
function invalid() {return new Error('The saved action workflow could not be verified. Reload this chat before retrying.');}
function id(value) {if(typeof value!=='string'||!UUID.test(value))throw invalid();return value;}
export function parseWorkflow(value) {
  if(!value||value.version!=='v2-actions-v1'||!['awaiting_confirmation','running','failed','completed','cancelled'].includes(value.state)||
    !Number.isSafeInteger(value.customer_id)||value.customer_id<=0||typeof value.ready!=='boolean'||typeof value.can_resume!=='boolean'||
    !Array.isArray(value.pending_commands)||value.pending_commands.length>5||
    ![null,'resume_failed','cancelled','assistant_unavailable','interrupted'].includes(value.error_code))throw invalid();
  const pending_commands=value.pending_commands.map(id);
  if(new Set(pending_commands).size!==pending_commands.length||value.can_resume&&(!value.ready||pending_commands.length||['completed','cancelled'].includes(value.state)))throw invalid();
  if(value.lease_expires_at!==null&&(typeof value.lease_expires_at!=='string'||!Number.isFinite(Date.parse(value.lease_expires_at))))throw invalid();
  return {version:value.version,turn_id:id(value.turn_id),session_id:id(value.session_id),case_id:id(value.case_id),customer_id:value.customer_id,
    state:value.state,ready:value.ready,can_resume:value.can_resume,pending_commands,lease_expires_at:value.lease_expires_at,error_code:value.error_code};
}
export function parsePausedWorkflow(value,sessionId) {
  const workflow=parseWorkflow(value?.workflow);
  if(workflow.session_id!==sessionId||workflow.state!=='awaiting_confirmation'||!workflow.ready||!Array.isArray(value.actions)||!value.actions.length||value.actions.length>5)throw invalid();
  const ids=value.actions.map(action=>{
    if(action?.type!=='card.temporary_block'||typeof action.summary!=='string'||!action.summary.trim()||typeof action.expires_at!=='string'||!Number.isFinite(Date.parse(action.expires_at)))throw invalid();
    return id(action.command_id);
  });
  if(new Set(ids).size!==ids.length||ids.length!==workflow.pending_commands.length||ids.some(commandId=>!workflow.pending_commands.includes(commandId)))throw invalid();
  if(value.preview!==undefined)parsePreview(value.preview,workflow);
  return workflow;
}
// The paused reply shows every section; only urgent fraud sections may carry one of the pending block offers.
export function parsePreview(value,workflow) {
  const sections=value?.sections;
  if(!Array.isArray(sections)||!sections.length||sections.length>5||typeof value.answer!=='string')throw invalid();
  const offered=sections.filter(section=>{
    if(typeof section?.task_id!=='string'||typeof section.answer!=='string')throw invalid();
    const action=section.pending_action;
    if(action===null||action===undefined)return false;
    if(section.domain!=='fraud'||section.priority!=='P0'||action.type!=='card.temporary_block'||typeof action.summary!=='string'||!action.summary.trim()||!Number.isFinite(Date.parse(action.expires_at)))throw invalid();
    id(action.command_id);return true;
  }).map(section=>section.pending_action.command_id);
  if(new Set(offered).size!==offered.length||offered.length!==workflow.pending_commands.length||offered.some(commandId=>!workflow.pending_commands.includes(commandId)))throw invalid();
  return value;
}
export async function loadWorkflowPreview(turnId,headers,signal) {
  const response=await fetch(`${API_URL}/workflows/${id(turnId)}/preview`,{headers,signal});
  const body=await response.json();
  if(!response.ok)throw new Error(typeof body?.error?.message==='string'?body.error.message:'The waiting reply could not be loaded. Reload the saved chat.');
  const workflow=parseWorkflow(body?.workflow);
  if(workflow.turn_id!==turnId)throw invalid();
  return {workflow,preview:parsePreview(body.preview,workflow)};
}
export async function resumeWorkflow(turnId,token,signal) {
  const response=await fetch(`${API_URL}/workflows/${id(turnId)}/resume`,{method:'POST',headers:{Authorization:`Bearer ${token}`},signal});
  const body=await response.json();
  if(!response.ok)throw new Error(typeof body?.error?.message==='string'?body.error.message:'The reply could not be resumed. Reload its recorded status.');
  const workflow=parseWorkflow(body);
  if(workflow.turn_id!==turnId)throw invalid();
  return workflow;
}
