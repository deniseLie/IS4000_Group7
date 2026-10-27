import {useEffect,useRef,useState} from 'react';
import {supabase} from '../lib/supabase';
import {createActionCase,proposeAction} from '../lib/actions';
import {CONSENT_CATEGORIES,consentCategoryText} from '../lib/consentData';
import {displayName,formatDateTime} from '../lib/format';

export default function ConsentControls({registry,recipient,busy,onRefresh,onReviewActions}) {
  const [categories,setCategories]=useState(['profile']);
  const [days,setDays]=useState(30);
  const [saving,setSaving]=useState(false);
  const [outcome,setOutcome]=useState(null);
  const [error,setError]=useState('');
  const active=useRef(null);
  useEffect(()=>()=>active.current?.abort(),[]);
  async function submit(query) {
    const controller=new AbortController();active.current=controller;setSaving(true);setError('');setOutcome(null);
    try {
      const {data,error:sessionError}=await supabase.auth.getSession();
      if(sessionError)throw sessionError;
      if(!data.session)throw new Error('Please sign in again to review consent.');
      const token=data.session.access_token;
      const scope=await createActionCase({session_id:crypto.randomUUID(),case_id:crypto.randomUUID()},token,controller.signal);
      const saved=await proposeAction({...query,command_id:crypto.randomUUID(),task_id:crypto.randomUUID(),case_id:scope.case_id},token,controller.signal);
      if(!controller.signal.aborted){setOutcome(saved);await onRefresh();}
    } catch(failure) {if(failure.name!=='AbortError'&&!controller.signal.aborted)setError(failure.message);}
    finally {if(!controller.signal.aborted)setSaving(false);}
  }
  const pending=outcome?.command.state==='proposed';
  const disabled=busy||saving||!!error||pending||!registry;
  return <section aria-label="Manage consent" className="relationship-details">
    <h3>Manage data-sharing consent</h3>
    <p>Record a request, then review the exact recipient, purpose, categories and expiry in Actions. Consent changes do not themselves share data.</p>
    {saving&&<p className="loading" role="status">Saving the recorded consent action… Closing this panel does not cancel it. Reopen Actions to verify its result.</p>}
    {error&&<p role="alert" data-testid="consent-action-error">{error} Refresh, then try again.</p>}
    {outcome&&<div className="command-review" data-testid="consent-action-outcome">
      <p>{pending?'The proposal is saved and still needs your confirmation in Actions.':outcome.receipt?.outcome==='succeeded'?'The consent request is recorded. Consent has not been granted.':'This request could not be saved because your relationship manager changed. Refresh, then try again.'}</p>
      {outcome.receipt&&<p>Receipt: <code>{outcome.receipt.receipt_id}</code></p>}
      {pending&&<button className="primary-button" data-testid="review-consent-actions" disabled={busy||saving} onClick={onReviewActions}>Review in Actions</button>}
    </div>}
    {recipient&&<form onSubmit={event=>{event.preventDefault();submit({type:'consent.request',target:{purpose:'rm_brief',recipient:recipient.rm_id,categories,duration_days:days}});}}>
      <p>Recipient: <strong>{displayName(recipient.name)}</strong>. Purpose: relationship-manager briefing.</p>
      <fieldset disabled={disabled}><legend>Data categories for this request</legend>{Object.entries(CONSENT_CATEGORIES).map(([code,label])=><label className="review-summary" key={code}><input type="checkbox" data-testid={`consent-category-${code}`} checked={categories.includes(code)} onChange={event=>setCategories(previous=>event.target.checked?[...previous,code]:previous.filter(item=>item!==code))}/>{label}</label>)}</fieldset>
      <label>Requested duration <select data-testid="consent-duration" disabled={disabled} value={days} onChange={event=>setDays(Number(event.target.value))}>{[1,7,30,90,365].map(value=><option key={value} value={value}>{value} {value===1?'day':'days'}</option>)}</select></label>
      <p>The grant expiry is fixed when the request is recorded. Confirming later does not extend it.</p>
      <button className="primary-button" data-testid="request-consent" disabled={disabled||categories.length===0} type="submit">Record consent request</button>
    </form>}
    {registry&&<>
      <h4>Consent requests</h4>
      <p>Showing {registry.requests.length} of {registry.requests_total} requests, with pending requests first.</p>
      {registry.requests.length===0&&<p data-testid="consent-requests-empty">No consent requests are recorded.</p>}
      <ul className="relationship-list" data-testid="consent-requests">{registry.requests.map(item=><li key={item.request_id} data-consent-request-id={item.request_id}>
        <p>{item.recipient_name?displayName(item.recipient_name):item.recipient} · Purpose: {item.purpose}</p><p>{consentCategoryText(item.categories)}</p>
        <p>Request status: <strong data-testid="consent-request-status">{item.status}</strong></p>
        <p>{item.grant_expires_at?`Requested grant expiry: ${formatDateTime(item.grant_expires_at)}`:'This legacy request has no reviewed expiry; record a new request to grant consent.'}</p>
        {item.status==='pending'&&item.grant_expires_at&&item.case_id&&<button className="bank-chat-logout" data-testid="prepare-consent-grant" disabled={disabled} onClick={()=>submit({type:'consent.grant',target:{request_id:item.request_id}})}>Review grant</button>}
      </li>)}</ul>
      <h4>Recorded grants</h4>
      <p>Showing {registry.grants.length} of {registry.grants_total} grants. Active grants appear first; refresh after revoking to see any additional grants. Each grant must be revoked separately.</p>
      {registry.grants.length===0&&<p data-testid="consent-grants-empty">No grants are recorded.</p>}
      <ul className="relationship-list" data-testid="consent-grants">{registry.grants.map(item=><li key={item.grant_id} data-consent-grant-id={item.grant_id}>
        <p>{item.recipient_name?displayName(item.recipient_name):item.recipient} · Purpose: {item.purpose}</p><p>{consentCategoryText(item.categories)}</p>
        <p>Current status: <strong data-testid="consent-grant-status">{item.revoked_at?'revoked':item.active?'active':'inactive'}</strong></p>
        <p>{item.expires_at?`Grant expiry: ${formatDateTime(item.expires_at)}`:'This recorded grant has no expiry.'}</p>
        {!item.revoked_at&&<button className="bank-chat-logout" data-testid="prepare-consent-revoke" disabled={disabled} onClick={()=>submit({type:'consent.revoke',target:{grant_id:item.grant_id}})}>Review revocation</button>}
      </li>)}</ul>
      <p className="source-notice">Revoking one grant does not revoke other grants or erase records that were already shared.</p>
    </>}
    <button className="bank-chat-logout" data-testid="refresh-consent-records" disabled={busy||saving} onClick={()=>{setError('');setOutcome(null);onRefresh();}}>Refresh</button>
  </section>;
}
