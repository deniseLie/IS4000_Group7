import {useEffect,useRef,useState} from 'react';
import {supabase} from '../lib/supabase';
import {createActionCase,proposeAction} from '../lib/actions';
import {consentCategoryText} from '../lib/consentData';

export default function AgendaSharing({agenda,registry,consentBusy,onReviewActions}) {
  const [grantId,setGrantId]=useState('');
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [outcome,setOutcome]=useState(null);
  const active=useRef(null);
  useEffect(()=>()=>active.current?.abort(),[]);
  const now=Date.now();
  const grants=(registry?.grants||[]).filter(grant=>grant.active&&grant.revoked_at===null&&Date.parse(grant.granted_at)<=now&&
    (grant.expires_at===null||Date.parse(grant.expires_at)>now)&&grant.purpose==='rm_brief'&&grant.recipient===agenda.advisor_id&&
    agenda.categories.every(category=>grant.categories.includes(category)));
  const selected=grants.find(grant=>grant.grant_id===grantId);
  async function propose(event) {
    event.preventDefault();
    const controller=new AbortController();active.current=controller;setBusy(true);setError('');setOutcome(null);
    try {
      const {data,error:failure}=await supabase.auth.getSession();
      if(failure)throw failure;
      if(!data.session)throw new Error('Please sign in again to review agenda sharing.');
      const token=data.session.access_token;
      const scope=await createActionCase({session_id:crypto.randomUUID(),case_id:crypto.randomUUID()},token,controller.signal);
      const result=await proposeAction({command_id:crypto.randomUUID(),case_id:scope.case_id,task_id:crypto.randomUUID(),type:'rm.share_summary',
        target:{agenda_id:agenda.agenda_id,agenda_revision:agenda.revision,advisor_id:agenda.advisor_id,consent_grant_id:grantId}},token,controller.signal);
      if(!controller.signal.aborted)setOutcome(result);
    } catch(failure) {if(failure.name!=='AbortError'&&!controller.signal.aborted)setError(failure.message);}
    finally {if(!controller.signal.aborted)setBusy(false);}
  }
  return <section className="relationship-details" aria-label="Share saved agenda" data-testid="agenda-sharing">
    <h4>Review a saved agenda snapshot</h4>
    <p>Recipient: <strong>{agenda.recipient_name}</strong>. Saved revision: {agenda.revision}. Purpose: relationship-manager briefing.</p>
    <p>Data included: {consentCategoryText(agenda.categories)}. Later draft edits do not change a previously shared snapshot.</p>
    <pre className="agenda-content" data-testid="agenda-sharing-preview">{agenda.content}</pre>
    {consentBusy&&<p className="loading" role="status">Checking recorded consent…</p>}
    {!consentBusy&&registry&&grants.length===0&&<p data-testid="agenda-sharing-no-consent">Record and confirm one consent request above covering all these categories for this recipient, then reopen this view.</p>}
    <form onSubmit={propose}>
      <label>Consent grant <select data-testid="agenda-sharing-grant" value={grantId} disabled={busy||consentBusy||!registry||!!error||!!outcome} onChange={event=>setGrantId(event.target.value)}>
        <option value="">Select an active grant</option>{grants.map(grant=><option key={grant.grant_id} value={grant.grant_id}>{consentCategoryText(grant.categories)} · {grant.expires_at?`Expires ${new Date(grant.expires_at).toLocaleString()}`:'No expiry'}</option>)}
      </select></label>
      {selected&&<p>Selected grant: <code>{selected.grant_id}</code>. The server rechecks its scope, expiry and revocation when you confirm.</p>}
      <button className="primary-button" data-testid="propose-agenda-sharing" disabled={busy||consentBusy||!selected||!!error||!!outcome}>Prepare sharing proposal</button>
    </form>
    {busy&&<p className="loading" role="status">Saving the proposal… Closing this panel does not cancel a submitted proposal. Check Actions for its recorded status.</p>}
    {error&&<p role="alert" data-testid="agenda-sharing-error">{error} Reopen this view and check Actions before retrying.</p>}
    {outcome&&<div data-testid="agenda-sharing-outcome"><p>The sharing proposal is recorded. Review its saved content and confirm in Actions to share this snapshot.</p>
      <button className="primary-button" data-testid="review-agenda-sharing" onClick={onReviewActions}>Review in Actions</button></div>}
  </section>;
}
