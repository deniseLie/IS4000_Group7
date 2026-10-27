import {useEffect,useRef,useState} from 'react';
import {supabase} from '../lib/supabase';
import {createActionCase,proposeAction} from '../lib/actions';
import {displayName} from '../lib/format';

export default function RequestIntake({recipient,recipientBusy,onReviewActions}) {
  const [what,setWhat]=useState('');
  const [urgency,setUrgency]=useState('normal');
  const [deadline,setDeadline]=useState('');
  const [country,setCountry]=useState('');
  const [currency,setCurrency]=useState('');
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [outcome,setOutcome]=useState(null);
  const active=useRef(null);
  useEffect(()=>()=>active.current?.abort(),[]);
  async function prepare(event) {
    event.preventDefault();const controller=new AbortController();active.current=controller;setBusy(true);setError('');
    try {
      const {data,error:failure}=await supabase.auth.getSession();if(failure)throw failure;
      if(!data.session)throw new Error('Please sign in again to prepare a relationship request.');
      const token=data.session.access_token;
      const scope=await createActionCase({session_id:crypto.randomUUID(),case_id:crypto.randomUUID()},token,controller.signal);
      const saved=await proposeAction({command_id:crypto.randomUUID(),case_id:scope.case_id,task_id:crypto.randomUUID(),type:'requests.create',
        target:{advisor_id:recipient.rm_id,what,urgency,deadline:deadline||null,country:country||null,currency:currency||null}},token,controller.signal);
      if(!controller.signal.aborted)setOutcome(saved);
    } catch(failure) {if(failure.name!=='AbortError'&&!controller.signal.aborted)setError(failure.message);}
    finally {if(!controller.signal.aborted)setBusy(false);}
  }
  const disabled=busy||recipientBusy||!recipient||!!error||!!outcome;
  return <section className="relationship-details" data-testid="request-intake">
    <h4>Prepare a relationship request</h4>
    <p>{recipient?`Recipient: ${displayName(recipient.name)}.`:'A current relationship manager is required to prepare a request.'} This records a request in the demo. It does not perform a financial transaction or contact a real advisor.</p>
    <form className="records-filters" onSubmit={prepare}>
      <label className="request-details-field">Request details <textarea data-testid="request-details" required maxLength={1000} value={what} disabled={disabled} onChange={event=>setWhat(event.target.value)}/></label>
      <label>Urgency <select data-testid="request-urgency" value={urgency} disabled={disabled} onChange={event=>setUrgency(event.target.value)}>{['low','normal','high'].map(value=><option key={value}>{value}</option>)}</select></label>
      <label>Requested deadline (optional) <input data-testid="request-deadline" type="date" value={deadline} disabled={disabled} onChange={event=>setDeadline(event.target.value)}/></label>
      <label>Country reference code (optional, two uppercase letters) <input data-testid="request-country" pattern="[A-Z]{2}" maxLength={2} value={country} disabled={disabled} onChange={event=>setCountry(event.target.value)}/></label>
      <label>Currency reference code (optional, three uppercase letters) <input data-testid="request-currency" pattern="[A-Z]{3}" maxLength={3} value={currency} disabled={disabled} onChange={event=>setCurrency(event.target.value)}/></label>
      <p>A requested deadline or urgency is not a service commitment. Review the saved, redacted details in Actions before confirming submission.</p>
      <button className="primary-button" data-testid="prepare-request" disabled={disabled||!what.trim()}>Prepare request for review</button>
    </form>
    {busy&&<p className="loading" role="status">Recording a proposal… Closing this panel does not cancel it. Check Actions before retrying.</p>}
    {error&&<p role="alert" data-testid="request-intake-error">{error} Reopen this view and check Actions before retrying.</p>}
    {outcome&&<div data-testid="request-proposal"><p>The proposal is recorded. Confirm or reject its exact details in Actions. No request has been submitted yet.</p><button className="primary-button" data-testid="review-request-actions" onClick={onReviewActions}>Review in Actions</button></div>}
  </section>;
}
