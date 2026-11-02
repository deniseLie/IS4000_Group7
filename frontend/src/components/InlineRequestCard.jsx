import {useEffect,useState} from 'react';
import Icon from './Icon';
import {supabase} from '../lib/supabase';
import {loadCategories,requestFailed} from '../lib/ticketData';
import {confirmAction,rejectAction} from '../lib/actions';
import {cardLimitDraftSummary,missingTicketFields,prepareTicketAction} from '../lib/ticketWorkflow';

async function accessToken() {
  const {data,error}=await supabase.auth.getSession();
  if(error)throw error;
  if(!data.session)throw new Error('Please sign in before submitting a request.');
  return data.session.access_token;
}
function labelValue(field,value) {
  if(!value)return 'Not provided';
  if(field?.key==='new_limit') {
    const number=Number(value);
    return Number.isFinite(number)?`SGD ${number.toLocaleString('en-SG')}`:value;
  }
  if(field?.kind?.startsWith('date')&&/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    const [y,m,d]=value.split('-').map(Number);
    const result=new Date(Date.UTC(y,m-1,d));
    return result.toLocaleDateString('en-SG',{day:'numeric',month:'long',year:'numeric',timeZone:'UTC'});
  }
  return value;
}
// An inline review card is never an unconditional ticket creation. The card
// first validates local inputs, then proposes a server-validated immutable
// command, then confirms that exact server proposal after explicit approval.
export default function InlineRequestCard({prefill={},onDismiss,onSuccess,onProposalChange}) {
  const [categories,setCategories]=useState(null);
  const [category,setCategory]=useState(prefill.category||'');
  const [values,setValues]=useState(prefill.details||{});
  const [summary,setSummary]=useState(prefill.summary||'');
  const [urgency,setUrgency]=useState('normal');
  const [summaryEdited,setSummaryEdited]=useState(false);
  const [editing,setEditing]=useState(!prefill.summary||prefill.category==='statement_request');
  const [proposal,setProposal]=useState(null);
  const [receipt,setReceipt]=useState(null);
  const [acknowledged,setAcknowledged]=useState(false);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  useEffect(()=>{
    const controller=new AbortController();
    (async()=>{try {
      const list=await loadCategories(await accessToken(),controller.signal);
      if(controller.signal.aborted)return;
      const allowed=list.filter(item=>item.customer_raisable);
      setCategories(allowed);
      setCategory(current=>allowed.find(c=>c.category===current)?.category||allowed[0]?.category||'');
    } catch(err) {if(!controller.signal.aborted&&err.name!=='AbortError')setError(err.message);}})();
    return ()=>controller.abort();
  },[]);
  const chosen=categories?.find(item=>item.category===category);
  const missing=missingTicketFields(chosen,values);
  const complete=!!chosen&&!missing.length&&!!summary.trim();
  const proposed=proposal?.command;
  const shown=proposed?.target;
  function changeField(key,value) {
    const updated={...values,[key]:value,...(key==='duration'&&value==='permanent'?{until:''}:{})};
    setValues(updated);
    if(!summaryEdited&&category==='card_limit_change')setSummary(cardLimitDraftSummary(updated,prefill.selectedCardLabel));
  }
  async function propose(event) {
    event?.preventDefault();
    if(!complete||busy||proposal||receipt)return;
    setBusy(true);setError('');
    try {
      const saved=await prepareTicketAction({category:chosen,summary,urgency,values},await accessToken());
      if(saved.command.state!=='proposed'||saved.command.target?.category!==chosen.category)throw new Error('The prepared request could not be verified.');
      setProposal(saved);onProposalChange?.(saved.command.command_id);setEditing(false);setAcknowledged(false);
    }catch(err){setError(requestFailed(err));}finally{setBusy(false);}
  }
  async function confirm() {
    if(!proposed||!acknowledged||busy||receipt)return;
    setBusy(true);setError('');
    try {
      const result=await confirmAction(proposed.command_id,await accessToken());
      if(result.receipt?.outcome!=='succeeded'||result.command.type!=='ticket.create'||result.command.command_id!==proposed.command_id)throw new Error('The bank did not confirm this request. Check My Requests before trying again.');
      setReceipt(result.receipt.result);onProposalChange?.(null);
      onSuccess?.(result.receipt.result);
    }catch(err){setError('Unable to verify the request submission. Check My Requests or Actions before trying again. '+err.message);}finally{setBusy(false);}
  }
  async function revise() {
    if(busy||receipt)return;
    if(proposed) {
      setBusy(true);setError('');
      try {await rejectAction(proposed.command_id,await accessToken());setProposal(null);onProposalChange?.(null);setAcknowledged(false);setEditing(true);}
      catch(err){setError('Could not cancel the pending proposal. Please retry before editing. '+err.message);}
      finally{setBusy(false);}
    }else setEditing(true);
  }
  async function discard() {
    if(busy)return;
    if(proposed&&!receipt) {
      setBusy(true);setError('');
      try {await rejectAction(proposed.command_id,await accessToken());}
      catch(err){setError('Unable to discard the pending proposal. Open Actions to review it. '+err.message);setBusy(false);return;}
      onProposalChange?.(null);setBusy(false);
    }
    onDismiss?.();
  }
  const detailEntries=chosen?.fields||[];
  const readOnly=busy||!!proposal||!!receipt;
  return <section className="inline-request-card" data-testid="inline-request-card" aria-label="Review your request">
    <div className="inline-request-title"><Icon name="clip"/><div><strong>Review your request</strong><span>{receipt?'Submitted':proposal?'Awaiting your confirmation':'Draft — not submitted'}</span></div></div>
    {!categories&&!error&&<p role="status">Loading supported request categories…</p>}
    {categories&&!chosen&&<p role="alert">No supported request categories are available right now.</p>}
    {chosen&&<>
      {(editing&&!proposal&&!receipt)?<form className="inline-request-edit" data-testid="inline-request-edit" onSubmit={propose}>
        <label>Request type<select data-testid="inline-ticket-category" disabled={readOnly} value={category} onChange={event=>{setCategory(event.target.value);setValues({});setSummary('');setSummaryEdited(false);}}>{categories.map(item=><option key={item.category} value={item.category}>{item.label}</option>)}</select></label>
        <p className="inline-request-hint">{chosen.description} The {chosen.owner_team.toLowerCase()} handles this request.</p>
        {prefill.selectedCardLabel&&category==='card_limit_change'&&<p className="inline-request-hint">Selected in chat: {prefill.selectedCardLabel}. Enter the last 4 card digits to verify it.</p>}
        <div className="inline-request-fields">{chosen.fields.map(field=><label key={field.key}>{field.label}{field.required||(field.key==='until'&&values.duration==='temporary')?' *':''}
          {field.kind==='choice'?<select data-testid={`inline-ticket-field-${field.key}`} disabled={readOnly} required={field.required} value={values[field.key]||''} onChange={event=>changeField(field.key,event.target.value)}><option value="">Choose…</option>{field.choices.map(option=><option key={option} value={option}>{option}</option>)}</select>
            :<input data-testid={`inline-ticket-field-${field.key}`} value={values[field.key]||''} disabled={readOnly} required={field.required||(field.key==='until'&&values.duration==='temporary')} type={field.kind.startsWith('date')?'date':'text'} inputMode={field.kind==='amount'?'decimal':field.kind==='card'?'numeric':undefined} pattern={field.kind==='card'?'[0-9]{4}':field.kind==='amount'?'[0-9]{1,9}(\\.[0-9]{1,2})?':undefined} maxLength={field.kind==='card'?4:300} onChange={event=>changeField(field.key,event.target.value)}/>}</label>)}</div>
        <label>Short summary<textarea data-testid="inline-ticket-summary" maxLength={500} required value={summary} disabled={readOnly} onChange={event=>{setSummary(event.target.value);setSummaryEdited(true);}}/></label>
        <label>Urgency<select value={urgency} disabled={readOnly} onChange={event=>setUrgency(event.target.value)}>{['low','normal','high'].map(option=><option key={option}>{option}</option>)}</select></label>
        {missing.length>0&&<p className="inline-request-hint">Still needed: {missing.join(', ')}.</p>}
        <div className="inline-request-buttons"><button type="button" className="bank-chat-logout" disabled={busy} onClick={discard}>Cancel</button><button className="primary-button" data-testid="inline-prepare-ticket" disabled={busy||!complete}>Continue to confirmation</button></div>
      </form>:<>
        <dl className="inline-request-summary" data-testid="inline-request-summary">
          <div><dt>Request type</dt><dd>{chosen.label}</dd></div>
          {category==='card_limit_change'&&prefill.selectedCardLabel&&<div><dt>Card</dt><dd>{prefill.selectedCardLabel}{values.card?` •••• ${values.card}`:' · last 4 digits to verify'}</dd></div>}
          {(shown?Object.entries(shown.details):detailEntries.filter(f=>values[f.key]).map(f=>[f.key,values[f.key]])).filter(([key])=>!(key==='card'&&prefill.selectedCardLabel)).map(([key,value])=><div key={key}><dt>{detailEntries.find(f=>f.key===key)?.label||key.replaceAll('_',' ')}</dt><dd>{labelValue(detailEntries.find(f=>f.key===key),value)}</dd></div>)}
          <div><dt>Summary</dt><dd>{shown?.summary||summary}</dd></div>
          <div><dt>Responsible team</dt><dd>{shown?.owner_team||chosen.owner_team}</dd></div>
          <div><dt>Urgency</dt><dd>{shown?.urgency||urgency}</dd></div>
        </dl>
        {!proposal&&!receipt&&<div className="inline-request-buttons"><button type="button" className="bank-chat-logout" onClick={discard}>Dismiss</button><button type="button" className="bank-chat-logout" onClick={revise}>Edit details</button><button type="button" className="primary-button" data-testid="inline-prepare-ticket" disabled={busy||!complete} onClick={propose}>{complete?'Continue to confirmation':'Complete required details'}</button></div>}
        {!proposal&&!receipt&&missing.length>0&&<p className="inline-request-hint">Still needed: {missing.join(', ')}. Select Edit details to provide these.</p>}
        {proposal&&!receipt&&<>
          <p className="inline-request-hint">This draft has not been submitted. Confirming sends the exact details above to the responsible team; it does not approve the request.</p>
          <label className="inline-request-ack"><input type="checkbox" checked={acknowledged} disabled={busy} data-testid="inline-ticket-ack" onChange={event=>setAcknowledged(event.target.checked)}/> I have checked the request details and want to submit them.</label>
          <div className="inline-request-buttons"><button type="button" className="bank-chat-logout" disabled={busy} onClick={discard}>Cancel draft</button><button type="button" className="bank-chat-logout" disabled={busy} onClick={revise}>Edit details</button><button type="button" className="primary-button" data-testid="inline-confirm-ticket" disabled={busy||!acknowledged} onClick={confirm}>Confirm &amp; submit</button></div>
        </>}
        {receipt&&<div role="status" data-testid="inline-ticket-receipt" className="inline-request-receipt"><strong>{receipt.created?'Request submitted':'Existing request found'}</strong><p>Reference <code>{receipt.reference}</code> · {receipt.owner_team} · Status: {receipt.status.replaceAll('_',' ')}.</p><p>The responsible team makes the decision; no banking change has been approved by this chat.</p><button type="button" className="bank-chat-logout" onClick={discard}>Close review</button></div>}
      </>}
    </>}
    {busy&&<p role="status">{proposal?'Updating your request…':'Preparing your request…'}</p>}
    {error&&<p role="alert" className="inline-request-error">{error}</p>}
  </section>;
}
