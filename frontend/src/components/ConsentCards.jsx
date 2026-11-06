import {useEffect,useRef,useState} from 'react';
import {supabase} from '../lib/supabase';
import {confirmAction,createActionCase,proposeAction} from '../lib/actions';
import {DELETE_ALL,DELETE_ALL_NOTE,LOOKBACK,TOPIC_AREAS,accessLine,alwaysShared,askLine,cardState,choiceSummary,defaultChoice,grantTarget,latestEnd,
  loadAccessLog,loadAsks,loadSharing,openAsks,plusDays,revokeConsequence,scopeText,untilText} from '../lib/consentSharing';
import {firstName,formatDate,formatDateTime} from '../lib/format';

// Data sharing (consent v2, build plan 6e): a card per category she can switch (what is shared, until when, and what
// changes at once if she stops), the always-shared list, her RM's asks, who looked and when, and Delete all my data.
// Each change is one ledger action she confirms here; the receipt is in Actions.
export default function ConsentCards({onReviewActions,onChanged}) {
  const [sharing,setSharing]=useState(null),[asks,setAsks]=useState([]),[log,setLog]=useState(null);
  const [busy,setBusy]=useState(true),[saving,setSaving]=useState(false),[error,setError]=useState(''),[done,setDone]=useState('');
  const [editing,setEditing]=useState(null),[choice,setChoice]=useState(null),[stopping,setStopping]=useState(null),[deleting,setDeleting]=useState(false);
  const [all,setAll]=useState(false);
  const active=useRef(null);
  useEffect(()=>{refresh();return ()=>active.current?.abort();},[]);
  async function token() {
    const {data,error:sessionError}=await supabase.auth.getSession();
    if(sessionError)throw sessionError;
    if(!data.session)throw new Error('Please sign in again to review data sharing.');
    return data.session.access_token;
  }
  async function refresh() {
    const controller=new AbortController();active.current=controller;setBusy(true);setError('');
    try {
      const key=await token();
      const [found,asked,seen]=await Promise.all([loadSharing(key,controller.signal),loadAsks(key,controller.signal),loadAccessLog(key,controller.signal)]);
      if(!controller.signal.aborted){setSharing(found);setAsks(asked);setLog(seen);}
    } catch(failure) {if(failure.name!=='AbortError'&&!controller.signal.aborted){setError(failure.message);setSharing(null);}}
    finally {if(!controller.signal.aborted)setBusy(false);}
  }
  // Propose, then her click here confirms it (she reviewed the card); a receipt that did not succeed means something changed first.
  async function act(type,target,message) {
    const controller=new AbortController();active.current=controller;setSaving(true);setError('');setDone('');
    try {
      const key=await token();
      const scope=await createActionCase({session_id:crypto.randomUUID(),case_id:crypto.randomUUID()},key,controller.signal);
      let result=await proposeAction({type,target,command_id:crypto.randomUUID(),task_id:'data-sharing',case_id:scope.case_id},key,controller.signal);
      if(result.command.state==='proposed')result=await confirmAction(result.command.command_id,key,controller.signal);
      if(controller.signal.aborted)return;
      if(result.receipt?.outcome!=='succeeded')throw new Error('This change did not go through because something changed first. Refresh, then try again.');
      setDone(message);setEditing(null);setStopping(null);setDeleting(false);await refresh();onChanged?.();
    } catch(failure) {if(failure.name!=='AbortError'&&!controller.signal.aborted)setError(failure.message);}
    finally {if(!controller.signal.aborted)setSaving(false);}
  }
  function open(card) {setEditing(card.code);setStopping(null);setChoice(defaultChoice(card,sharing));setError('');setDone('');}
  function save(event,card) {
    event.preventDefault();
    let target;
    try {target=grantTarget(card,choice,sharing);} catch(failure) {setError(failure.message);return;}
    act('consent.grant',target,`Saved. ${choiceSummary(card,choice,sharing,formatDate)}`);
  }
  const rm=sharing?.rm?firstName(sharing.rm.name):'your relationship manager',off=busy||saving;
  const cards=sharing?sharing.categories.filter(c=>c.gated):[],rows=log?.rows||[];
  return <section className="page-section" aria-label="What you share" data-testid="consent-cards">
    <h2 className="section-label">What you share with {rm}</h2>
    {busy&&!sharing&&<p className="loading" role="status">Loading your data sharing…</p>}
    {error&&<p role="alert" data-testid="consent-cards-error">{error}</p>}
    {done&&<p role="status" data-testid="consent-cards-done">{done} <button type="button" className="bank-chat-logout" onClick={onReviewActions}>See the receipt in Activity</button></p>}
    {sharing&&!sharing.rm&&<p className="page-empty" data-testid="consent-cards-no-rm">No relationship manager is assigned to you, so nothing below is shared.</p>}
    {sharing&&openAsks(asks).map(ask=><div key={ask.ask_id} className="command-review" data-testid="consent-ask">
      <p>{askLine(ask,formatDateTime)} If you do nothing, this request closes on {formatDate(ask.lapses_at)}.</p>
      <div className="panel-actions">
        <button type="button" className="primary-button" data-testid="consent-ask-share" disabled={off} onClick={()=>open(sharing.categories.find(c=>c.code===ask.category))}>Choose what to share</button>
        <button type="button" className="bank-chat-logout" data-testid="consent-ask-decline" disabled={off}
          onClick={()=>act('consent.decline_ask',{ask_id:ask.ask_id},`${rm[0].toUpperCase()+rm.slice(1)} has been told you are not sharing this now.`)}>Not now</button>
      </div></div>)}
    {sharing&&<div className="consent-cards">{cards.map(card=>{const state=cardState(card),grant=card.grant;return <article key={card.code} className="access-card" data-testid={`consent-card-${card.code}`} data-on={state.on}>
      <h3>{card.label} <span className={`status-badge ${state.on?'active':''}`}><span className="dot"/>{state.label}</span></h3>
      <p>{card.description}</p>
      {grant&&<p data-testid="consent-card-scope">{scopeText(card.code,grant.scope,sharing.accounts)}{grant.basis!=='notice'&&<> · {untilText(grant,formatDateTime)}</>}</p>}
      {grant?.renewal_due&&<p className="page-note" data-testid="consent-card-renewal">This ends soon. Switch it on again if you want {rm} to keep seeing it.</p>}
      {!state.switchable&&card.reason&&<p className="page-note">{card.reason}</p>}
      {state.switchable&&sharing.rm&&editing!==card.code&&stopping!==card.code&&<div className="panel-actions">
        <button type="button" className={grant?'bank-chat-logout':'primary-button'} data-testid="consent-card-edit" disabled={off} onClick={()=>open(card)}>{grant?'Change':'Switch on'}</button>
        {grant&&<button type="button" className="bank-chat-logout" data-testid="consent-card-stop" disabled={off} onClick={()=>{setStopping(card.code);setEditing(null);setDone('');}}>Switch off</button>}
      </div>}
      {stopping===card.code&&grant&&<div className="command-review" data-testid="consent-card-consequence">
        <p>{revokeConsequence(card.code,rm[0].toUpperCase()+rm.slice(1))}</p>
        <div className="panel-actions">
          <button type="button" className="primary-button" data-testid="consent-card-stop-confirm" disabled={off}
            onClick={()=>act('consent.revoke',{grant_id:grant.grant_id},`Switched off. ${rm[0].toUpperCase()+rm.slice(1)} no longer sees your ${card.label.toLowerCase()}.`)}>Switch off now</button>
          <button type="button" className="bank-chat-logout" disabled={saving} onClick={()=>setStopping(null)}>Keep sharing</button>
        </div></div>}
      {editing===card.code&&choice&&<form className="command-review" data-testid="consent-card-form" onSubmit={event=>save(event,card)}>
        <fieldset disabled={off}>
          {card.code==='transaction_insights'?<>
            <legend>Which accounts</legend>
            <label className="review-summary"><input type="radio" name="accounts" checked={choice.accounts==='all'} onChange={()=>setChoice({...choice,accounts:'all'})}/>All my accounts and cards</label>
            <label className="review-summary"><input type="radio" name="accounts" checked={choice.accounts!=='all'} onChange={()=>setChoice({...choice,accounts:sharing.accounts.slice(0,1).map(a=>a.account_id)})}/>Only the accounts I choose (no card spending)</label>
            {choice.accounts!=='all'&&sharing.accounts.map(a=><label key={a.account_id} className="review-summary"><input type="checkbox" data-testid="consent-card-account" checked={choice.accounts.includes(a.account_id)}
              onChange={event=>setChoice({...choice,accounts:event.target.checked?[...choice.accounts,a.account_id]:choice.accounts.filter(n=>n!==a.account_id)})}/>{a.type} {a.number}</label>)}
            <label>How far back <select data-testid="consent-card-lookback" value={choice.lookback_months} onChange={event=>setChoice({...choice,lookback_months:Number(event.target.value)})}>
              {LOOKBACK.map(n=><option key={n} value={n}>Last {n} months</option>)}</select></label>
          </>:<>
            <legend>Which topic areas</legend>
            {Object.entries(TOPIC_AREAS).map(([code,label])=><label key={code} className="review-summary"><input type="checkbox" data-testid="consent-card-area" checked={choice.topic_areas.includes(code)}
              onChange={event=>setChoice({...choice,topic_areas:event.target.checked?[...choice.topic_areas,code]:choice.topic_areas.filter(a=>a!==code)})}/>{label}</label>)}
            <p className="page-note">Health, family and legal matters are never picked up.</p>
          </>}
          <label>Until <select data-testid="consent-card-until" value={choice.until} onChange={event=>setChoice({...choice,until:event.target.value})}>
            <option value="date">A date</option>{sharing.next_meeting&&<option value="meeting">My meeting on {formatDateTime(sharing.next_meeting.starts_at)} ends</option>}</select></label>
          {choice.until==='date'&&<label>End date <input type="date" data-testid="consent-card-date" min={plusDays(sharing.today,1)} max={latestEnd(sharing.today,card.max_months||12)}
            value={choice.expires_on} onChange={event=>setChoice({...choice,expires_on:event.target.value})}/></label>}
        </fieldset>
        <p>Nothing is shared until you confirm. You can switch it off here at any time.</p>
        <div className="panel-actions"><button type="submit" className="primary-button" data-testid="consent-card-save" disabled={off}>Confirm</button>
          <button type="button" className="bank-chat-logout" disabled={saving} onClick={()=>setEditing(null)}>Cancel</button></div>
      </form>}
    </article>;})}</div>}
    {sharing&&<><h3>Always shared with {rm}</h3>
      <ul className="relationship-list" data-testid="consent-always-shared">{alwaysShared(sharing).map(c=><li key={c.code}><strong>{c.label}</strong>{c.reason&&<> · {c.reason}</>}</li>)}</ul></>}
    {log&&<><h3>Who looked and when</h3>
      {rows.length===0?<p className="page-empty" data-testid="consent-access-empty">No relationship manager has looked at what you share yet.</p>:
      <ul className="relationship-list" data-testid="consent-access-log">{(all?rows:rows.slice(0,10)).map((row,i)=><li key={(row.call_id||'refused')+row.at+i}>
        <time dateTime={row.at}>{formatDateTime(row.at)}</time> · {accessLine(row,rm[0].toUpperCase()+rm.slice(1))}</li>)}</ul>}
      {rows.length>10&&!all&&<button type="button" className="bank-chat-logout" onClick={()=>setAll(true)}>Show all {rows.length}</button>}</>}
    {sharing&&<><h3>Delete all my data</h3>
      {!deleting?<button type="button" className="bank-chat-logout" data-testid="consent-delete-all" disabled={off} onClick={()=>{setDeleting(true);setDone('');}}>Delete all my data</button>:
      <div className="command-review" data-testid="consent-delete-all-review"><p>{DELETE_ALL_NOTE}</p>
        <div className="panel-actions"><button type="button" className="primary-button" data-testid="consent-delete-all-confirm" disabled={off}
          onClick={()=>act('ticket.create',DELETE_ALL,'Your request is with our data protection team. You can follow it in My requests.')}>Send the request</button>
          <button type="button" className="bank-chat-logout" disabled={saving} onClick={()=>setDeleting(false)}>Cancel</button></div></div>}</>}
  </section>;
}
