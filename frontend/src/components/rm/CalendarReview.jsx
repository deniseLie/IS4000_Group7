import {useRef,useState} from 'react';
import Icon from '../Icon';
import {DECLINE_REASONS,FORMAT_WORDS,availabilityNotice,availabilitySteps,decisionBody,decisionNotice,display,idKeeper,rmPost,runStep,slotProblemText,topicLabel} from '../../lib/rm';

const ANSWERS=[['accept','Accept one of the client’s times'],['counter','Propose another time'],['decline','Decline the request']];

// The Calendar agent's answer to a client's meeting request (build plan 3a): prepared from the records, recorded only when
// the RM approves it, through the same route as the Schedule page (one decision id per card, so a retry records once).
export function DecisionReview({card,token,latest}) {
  const s=card.suggested||{};
  const [choice,setChoice]=useState(s.decision||'accept');
  const [accept,setAccept]=useState(s.decision==='accept'&&s.starts_at?s.starts_at:'');
  const [propose,setPropose]=useState(s.decision==='counter'&&s.starts_at?s.starts_at:'');
  const [reason,setReason]=useState(s.reason||'');
  const [checked,setChecked]=useState(false);
  const [state,setState]=useState({busy:false,error:'',done:''});
  const ids=useRef(idKeeper()).current;
  async function approve() {
    setState({busy:true,error:'',done:''});
    try {
      const result=await rmPost(`/meeting-requests/${encodeURIComponent(card.request_id)}/decide`,
        decisionBody(ids.get(card.request_id),choice,{startsAt:choice==='accept'?accept:propose,reason:reason||null}),await token());
      ids.done(card.request_id);
      setState({busy:false,error:'',done:decisionNotice(result)||'Your answer is recorded.'});
    } catch(failure) {setState({busy:false,error:`Not recorded. ${failure.message}`,done:''});}
  }
  const ready=choice==='decline'?!!reason:!!(choice==='accept'?accept:propose);
  const locked=state.busy||!!state.done||!latest;
  return <section className="rmw-review decision" data-testid="rm-decision">
    <div className="rmw-review-head"><strong><Icon name="calendar"/>{state.done?'Answer recorded':'Meeting request – not answered yet'}</strong>
      <span className="category-badge">{card.overdue?`Overdue (was due ${card.respond_by_text})`:`Answer by ${card.respond_by_text}`}</span></div>
    <p className="rm-small">{display(card.client)} · {FORMAT_WORDS[card.format]||card.format||'Meeting'}{card.topics?.length?` · ${card.topics.map(topicLabel).join(', ')}`:''}</p>
    <label className="form-field">Your answer<select value={choice} disabled={locked} data-testid="rm-decision-choice" onChange={e=>setChoice(e.target.value)}>
      {ANSWERS.map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label>
    {choice==='accept'&&<label className="form-field">The client’s time<select value={accept} disabled={locked} data-testid="rm-decision-time" onChange={e=>setAccept(e.target.value)}>
      <option value="">Choose one of the client’s times</option>
      {card.preferred_times.map(t=><option key={t.starts_at} value={t.starts_at} disabled={!!t.problem}>{t.when}{t.problem?` (cannot accept: ${slotProblemText(t.problem)})`:''}</option>)}</select></label>}
    {choice==='counter'&&<label className="form-field">One of your free times (held for the client)<select value={propose} disabled={locked} data-testid="rm-decision-time" onChange={e=>setPropose(e.target.value)}>
      <option value="">Choose a free time</option>{card.free_times.map(t=><option key={t.starts_at} value={t.starts_at}>{t.when}</option>)}</select></label>}
    {choice==='decline'&&<label className="form-field">Reason (the client sees it)<select value={reason} disabled={locked} data-testid="rm-decision-reason" onChange={e=>setReason(e.target.value)}>
      <option value="">Choose a reason</option>{DECLINE_REASONS.map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label>}
    {!state.done&&<label className="review-summary"><input type="checkbox" data-testid="rm-decision-confirm" checked={checked} disabled={locked} onChange={e=>setChecked(e.target.checked)}/> I approve this answer. The client gets an Inbox notice.</label>}
    {state.error&&<p role="alert" className="rm-error">{state.error}</p>}
    {state.done&&<p role="status" className="rm-success" data-testid="rm-decision-done"><Icon name="check"/>{state.done}</p>}
    {!state.done&&<div className="panel-actions"><button type="button" className="primary-button" data-testid="rm-decision-approve" disabled={locked||!checked||!ready} onClick={approve}>
      {state.busy?'Recording…':'Approve answer'}<Icon name="check"/></button></div>}
  </section>;
}

// The Calendar agent's change to her availability (block, open again or weekly hours): each step runs through the
// Schedule page's routes when she approves; a retry after a refusal continues from the first step not yet made.
export function AvailabilityReview({card,token,latest}) {
  const steps=availabilitySteps(card);
  const [checked,setChecked]=useState(false);
  const [state,setState]=useState({busy:false,error:'',done:0});
  async function approve() {
    let done=state.done;
    setState({busy:true,error:'',done});
    try {const auth=await token();for(const step of steps.slice(done)){await runStep(step,auth);done+=1;}setState({busy:false,error:'',done});}
    catch(failure) {setState({busy:false,error:`Stopped after ${done} of ${steps.length} changes. ${failure.message}`,done});}
  }
  const finished=steps.length>0&&state.done===steps.length;
  const locked=state.busy||finished||!latest;
  return <section className="rmw-review availability" data-testid="rm-availability">
    <div className="rmw-review-head"><strong><Icon name="calendar"/>{finished?'Calendar changed':'Calendar change – not made yet'}</strong>
      <span className="category-badge">{steps.length} change{steps.length===1?'':'s'}</span></div>
    <ul className="rm-points">{steps.map((step,i)=><li key={i} className="rm-item">{step.label}{i<state.done&&<span className="rm-edited"> Done</span>}</li>)}</ul>
    {card.kept?.length>0&&<p className="rm-small">{card.kept.map(k=>`${k.when} stays booked with ${display(k.client)}`).join('; ')}: move it with the client first if you need that time.</p>}
    {!finished&&<label className="review-summary"><input type="checkbox" data-testid="rm-availability-confirm" checked={checked} disabled={locked} onChange={e=>setChecked(e.target.checked)}/> I approve these changes to my calendar.</label>}
    {state.error&&<p role="alert" className="rm-error">{state.error}</p>}
    {finished&&<p role="status" className="rm-success" data-testid="rm-availability-done"><Icon name="check"/>{availabilityNotice(card)}</p>}
    {!finished&&<div className="panel-actions"><button type="button" className="primary-button" data-testid="rm-availability-approve" disabled={locked||!checked||!steps.length} onClick={approve}>
      {state.busy?'Saving…':state.done?'Try the rest again':'Approve changes'}<Icon name="check"/></button></div>}
  </section>;
}
