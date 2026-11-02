import {useState} from 'react';
import Icon from '../Icon';
import {newId,rmPost,STATUS_WORDS} from '../../lib/rm';
import {redact} from '../../lib/chatTools';

// RM9: an AI draft is labelled as a draft, editable, and sent only when the RM approves this exact version.
// Asking the assistant for changes creates a new revision; an earlier draft is never treated as approved.
export default function DraftReview({draft,token,onChange,onAsk,latest}) {
  const [text,setText]=useState(draft.text);
  const [status,setStatus]=useState(draft.suggested_status||'');
  const [checked,setChecked]=useState(false);
  const [state,setState]=useState({busy:false,error:'',sent:null});
  const edited=text.trim()!==draft.text.trim();
  async function approve() {
    setState({busy:true,error:'',sent:null});
    const safe=redact(text.trim());
    if(safe!==text.trim()){setState({busy:false,error:'Remove card numbers, emails, ID numbers or codes before sending.',sent:null});return;}
    try {
      const sent=await rmPost('/messages',{message_id:newId(),customer_id:draft.customer_id,reference:draft.reference||null,body:safe,
        status:draft.can_change_status&&status?status:null,draft_revision:draft.revision,edited},await token());
      setState({busy:false,error:'',sent});
      onChange?.(null);
    } catch(failure) {setState({busy:false,error:`Not sent. ${failure.message}`,sent:null});}
  }
  const locked=state.busy||!!state.sent||!latest;
  return <section className="rmw-review draft" data-testid="rm-draft">
    <div className="rmw-review-head"><strong><Icon name="document"/>{state.sent?'Sent message':'AI draft – not sent'}</strong>
      <span className="category-badge">Revision {draft.revision}{edited?' · edited by you':''}</span></div>
    <p className="rm-small">To {draft.client}{draft.reference?` · on request ${draft.reference} (shown in their My requests)`:' · as a message in their Inbox'}</p>
    <textarea data-testid="rm-draft-text" rows={6} maxLength={500} value={text} disabled={locked} onChange={e=>{setText(e.target.value);setChecked(false);}}/>
    <small className="rm-small">{text.length}/500 · Edit freely, or ask the assistant in the chat to change it (for example “make it shorter”).</small>
    {draft.cautions?.length>0&&<ul className="rm-cautions">{draft.cautions.map(c=><li key={c}><Icon name="alert"/>{c}</li>)}</ul>}
    {draft.restricted&&<p className="rm-cautions"><Icon name="lock"/>This request is restricted to a specialist team; a message cannot be added to it.</p>}
    {draft.can_change_status&&<label className="form-field">Request status after sending (optional)
      <select value={status} disabled={locked} onChange={e=>setStatus(e.target.value)}><option value="">Keep as it is</option>
        {['in_review','waiting_on_client','resolved'].map(s=><option key={s} value={s}>{STATUS_WORDS[s]}{s===draft.suggested_status?' (suggested)':''}</option>)}</select></label>}
    {!state.sent&&<label className="review-summary"><input type="checkbox" data-testid="rm-draft-confirm" checked={checked} disabled={locked} onChange={e=>setChecked(e.target.checked)}/> I have reviewed this exact message and approve sending it to the client.</label>}
    {state.error&&<p role="alert" className="rm-error">{state.error}</p>}
    {state.sent&&<p role="status" className="rm-success" data-testid="rm-draft-sent"><Icon name="check"/>Sent {state.sent.at}{state.sent.reference?` on ${state.sent.reference}`:''}. Your approval is recorded{state.sent.status?`; status: ${STATUS_WORDS[state.sent.status]||state.sent.status}`:''}.</p>}
    {!state.sent&&<div className="panel-actions">
      <button type="button" className="bank-chat-logout" disabled={locked} onClick={()=>onAsk('Make the draft shorter and simpler.')}>Ask for a shorter version</button>
      <button type="button" className="primary-button" data-testid="rm-draft-approve" disabled={locked||!checked||draft.restricted||!text.trim()} onClick={approve}>{state.busy?'Sending…':'Approve and send'}<Icon name="send"/></button></div>}
  </section>;
}
