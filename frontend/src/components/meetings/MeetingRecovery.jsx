import {useEffect,useState} from 'react';

const problemText={meeting_revision_changed:'The meeting changed. Refresh before continuing.',job_not_failed:'This job is no longer failed.',
  job_type_not_retryable:'This job needs an operator review.',call_not_ended:'Document the call end first.',
  transcript_permission_unavailable:'Transcript permission is unavailable.',authorization_refusal_requires_new_source:'A refused source needs a new authorised submission.',
  review_and_correct_call_times_first:'Review and correct the call times first.',worker_claim_active:'Wait for the active worker attempt to finish.',source_changed:'The source changed. Use the current evidence.'};
const singaporeInput=value=>value?new Date(value).toLocaleString('sv-SE',{timeZone:'Asia/Singapore'}).replace(' ','T'):'';

export default function MeetingRecovery({recovery,busy,onPost}){
  const [reason,setReason]=useState(''),[start,setStart]=useState(''),[end,setEnd]=useState('');
  useEffect(()=>{setStart(singaporeInput(recovery.started_at));setEnd(singaporeInput(recovery.ended_at));},[recovery.started_at,recovery.ended_at]);
  const reviewed={revision:recovery.revision,expected_start:recovery.started_at,expected_end:recovery.ended_at,reason};
  const provider=recovery.provider_times;
  return <details className="meeting-evidence-form" data-testid="meeting-recovery"><summary>Review call times and failed attempts</summary>
    <p className="rm-small">Corrections require a reason and the current recorded version. Stored evidence and approved minutes stay unchanged.</p>
    <label className="form-field">Reason for recovery<textarea data-testid="meeting-recovery-reason" value={reason} maxLength={300} disabled={busy} onChange={event=>setReason(event.target.value)}/></label>
    {recovery.evidence_saved?<p>Call times are locked because evidence was saved. Submit revised notes or a new export to correct the evidence.</p>:
      provider?<div><p>Zoom start: {singaporeInput(provider.started_at).replace('T',' ')} · End: {provider.ended_at?singaporeInput(provider.ended_at).replace('T',' '):'Waiting for Zoom'}</p>
        {provider.conflict&&<p className="rmw-warn">Zoom and the manually recorded times differ. Review the verified provider times before continuing.</p>}
        <button className="bank-chat-logout" data-testid="meeting-reconcile-zoom-times" disabled={busy||!provider.ended_at||!reason.trim()} onClick={()=>onPost('/reconcile-zoom-times',reviewed,'Verified Zoom times reconciled. Review any failed attempt before retrying it.')}>Use these verified Zoom times</button></div>:
      recovery.ended_at&&<div className="rm-form-grid"><label className="form-field">Call start (Singapore time)<input type="datetime-local" step="1" data-testid="meeting-correct-start" value={start} disabled={busy} onChange={event=>setStart(event.target.value)}/></label>
        <label className="form-field">Call end (Singapore time)<input type="datetime-local" step="1" data-testid="meeting-correct-end" value={end} disabled={busy} onChange={event=>setEnd(event.target.value)}/></label>
        <button className="bank-chat-logout" data-testid="meeting-correct-times" disabled={busy||!start||!end||!reason.trim()} onClick={()=>onPost('/correct-call-times',{...reviewed,started_at:new Date(start+'+08:00').toISOString(),ended_at:new Date(end+'+08:00').toISOString()},'Reviewed call times corrected. A failed attempt still needs a separate retry approval.')}>Confirm corrected times</button></div>}
    {recovery.failed_jobs.map(job=><div key={job.event_id}><p>Attempt {job.event_id}: {job.error_code?.replaceAll('_',' ')}</p>
      {job.retry_problem&&<p>{problemText[job.retry_problem]}</p>}<button className="bank-chat-logout" data-testid={`meeting-retry-${job.event_id}`} disabled={busy||Boolean(job.retry_problem)||!reason.trim()} onClick={()=>onPost(`/jobs/${job.event_id}/retry`,{revision:recovery.revision,reason},'Reviewed attempt queued again with a new worker claim.')}>Approve another attempt</button></div>)}
  </details>;
}
