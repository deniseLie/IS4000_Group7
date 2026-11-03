const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
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
