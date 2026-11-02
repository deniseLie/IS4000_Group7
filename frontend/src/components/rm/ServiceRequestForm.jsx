import {useEffect,useState} from 'react';
import Icon from '../Icon';
import {newId,requestFromAction,rmPost} from '../../lib/rm';

// RM6: a service request prefilled from the approved interaction. The RM completes missing details, the backend checks
// fields and open duplicates, she confirms the client's identity check and authorisation, then submits. The reference is
// shown only after the request exists.
export default function ServiceRequestForm({action,customerId,noteId,token,onDone,onCancel}) {
  const [form,setForm]=useState(()=>requestFromAction(action,customerId));
  const [check,setCheck]=useState(null);
  const [verified,setVerified]=useState(false);
  const [state,setState]=useState({busy:false,error:''});
  const [key]=useState(newId);
  const category=action.request;
  async function validate(next=form) {
    try {setCheck(await rmPost('/requests/check',next,await token()));}
    catch(failure) {setCheck({valid:false,problem:failure.message,missing:[]});}
  }
  useEffect(()=>{validate();},[]);  // eslint-disable-line react-hooks/exhaustive-deps
  function set(patch){const next={...form,...patch};setForm(next);setCheck(null);}
  async function submit() {
    setState({busy:true,error:''});
    try {
      const result=await rmPost('/requests',{...form,request_key:key,client_verified:verified,note_id:noteId||null},await token());
      if(result.created)onDone(result);
      else setState({busy:false,error:result.duplicate?`Not submitted: ${result.duplicate.reference} of this type is already open (${result.duplicate.status.replace(/_/g,' ')}).`:'Not submitted.'});
    } catch(failure) {setState({busy:false,error:`Not submitted. ${failure.message}`});}
  }
  return <div className="rm-request-form" data-testid="rm-request-form">
    <p className="rm-small"><strong>{category.label}</strong> · goes to the {category.owner_team}. {category.decided_by}</p>
    <div className="rm-form-grid">
      {category.fields.map(field=><label key={field.key} className="form-field">{field.label}{field.required?'':' (optional)'}
        {field.kind==='choice'?<select value={form.details[field.key]||''} onChange={e=>set({details:{...form.details,[field.key]:e.target.value}})}>
            <option value="">Choose…</option>{field.choices.map(c=><option key={c}>{c}</option>)}</select>
          :<input value={form.details[field.key]||''} type={field.kind.startsWith('date')?'date':'text'} maxLength={field.kind==='card'?4:300}
            onChange={e=>set({details:{...form.details,[field.key]:e.target.value}})}/>}</label>)}
      <label className="form-field rm-span">Short summary (the client sees this in My requests)<textarea rows={2} maxLength={500} value={form.summary} onChange={e=>set({summary:e.target.value})}/></label>
      <label className="form-field">Urgency<select value={form.urgency} onChange={e=>set({urgency:e.target.value})}>{['low','normal','high'].map(u=><option key={u}>{u}</option>)}</select></label>
    </div>
    {category.documents?.length>0&&<p className="rm-small">The team may ask for: {category.documents.join('; ')}.</p>}
    {check&&!check.valid&&<ul className="rm-cautions" data-testid="rm-request-check">
      {check.missing?.map(m=><li key={m.key}><Icon name="alert"/>Missing: {m.label}</li>)}
      {check.duplicate&&<li><Icon name="alert"/>Possible duplicate: {check.duplicate.reference} ({check.duplicate.status.replace(/_/g,' ')}) – {check.duplicate.summary}</li>}
      {check.problem&&!check.missing?.length&&<li><Icon name="alert"/>{check.problem}</li>}</ul>}
    {check?.valid&&<p className="rm-success"><Icon name="check"/>All required details are filled in and no open request of this type exists.</p>}
    <label className="review-summary"><input type="checkbox" data-testid="rm-request-verified" checked={verified} onChange={e=>setVerified(e.target.checked)}/> I have verified the client’s identity under bank procedure and the client authorised this request.</label>
    {state.error&&<p role="alert" className="rm-error">{state.error}</p>}
    <div className="panel-actions"><button type="button" className="bank-chat-logout" onClick={onCancel} disabled={state.busy}>Cancel</button>
      <button type="button" className="bank-chat-logout" onClick={()=>validate()} disabled={state.busy}>Check again</button>
      <button type="button" className="primary-button" data-testid="rm-request-submit" disabled={state.busy||!verified||!check?.valid} onClick={submit}>{state.busy?'Submitting…':'Confirm and submit'}</button></div>
  </div>;
}
