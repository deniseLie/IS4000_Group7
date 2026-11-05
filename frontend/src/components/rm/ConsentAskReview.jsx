import {useRef,useState} from 'react';
import Icon from '../Icon';
import {askBody,askNotice,askReasons,display,idKeeper,rmPost} from '../../lib/rm';

// Consent v2 (0113): the copilot found a category the client has not shared and offers to ask her. Recorded only when the
// RM approves it (POST /rm/consent-requests, one key per card); the client decides in Data sharing and nothing is shared
// by the ask itself.
export default function ConsentAskReview({card,token,latest}) {
  const reasons=askReasons(card);
  const [reason,setReason]=useState(card.reason&&reasons.some(([k])=>k===card.reason)?card.reason:reasons[0][0]);
  const [checked,setChecked]=useState(false);
  const [state,setState]=useState({busy:false,error:'',done:''});
  const ids=useRef(idKeeper()).current;
  const key=`${card.customer_id}:${card.category}`;
  async function approve() {
    setState({busy:true,error:'',done:''});
    try {
      const result=await rmPost('/consent-requests',askBody(ids.get(key),card,reason),await token());
      ids.done(key);
      setState({busy:false,error:'',done:askNotice(result,card)||'Your request is recorded.'});
    } catch(failure) {setState({busy:false,error:`Not recorded. ${failure.message}`,done:''});}
  }
  const locked=state.busy||!!state.done||!latest;
  return <section className="rmw-review consent-ask" data-testid="rm-consent-ask">
    <div className="rmw-review-head"><strong><Icon name="shield"/>{state.done?'Request sent':`Ask ${display(card.client)||'the client'} to share`}</strong>
      <span className="category-badge">{card.category_label}</span></div>
    <p className="rm-small">{display(card.client)||'The client'} has not shared {String(card.category_label||'').toLowerCase()} with you. You can ask; they decide in Data sharing.
      {card.meeting_when?` The meeting: ${card.meeting_when}.`:''}</p>
    <label className="form-field">Why you ask (the client sees this)<select value={reason} disabled={locked} data-testid="rm-consent-ask-reason" onChange={e=>setReason(e.target.value)}>
      {reasons.map(([k,label])=><option key={k} value={k}>{label}</option>)}</select></label>
    {!state.done&&<label className="review-summary"><input type="checkbox" data-testid="rm-consent-ask-confirm" checked={checked} disabled={locked} onChange={e=>setChecked(e.target.checked)}/> I approve this request. The client gets an Inbox notice and can say no.</label>}
    {state.error&&<p role="alert" className="rm-error">{state.error}</p>}
    {state.done&&<p role="status" className="rm-success" data-testid="rm-consent-ask-done"><Icon name="check"/>{state.done}</p>}
    {!state.done&&<div className="panel-actions"><button type="button" className="primary-button" data-testid="rm-consent-ask-approve" disabled={locked||!checked} onClick={approve}>
      {state.busy?'Sending…':'Send request'}<Icon name="check"/></button></div>}
  </section>;
}
