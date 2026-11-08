import {useState} from 'react';
import TalkingPoints from './TalkingPoints';
import {LOOKBACK,TOPIC_AREAS,accountName,askLine,cardState,choiceSummary,defaultChoice,grantTarget,latestEnd,plusDays,revokeConsequence,scopeText,untilText} from '../lib/consentSharing';
import {formatDate,formatDateTime} from '../lib/format';

// One category one tap down from Data sharing: a switch with what is shared and until when, the form to change it, what
// changes at once if she switches it off, and for chat topics her topics in plain words (TalkingPoints).
export default function SharingCategory({card,sharing,rm,asks,topics,topicsError,busy,saving,act,status}) {
  const [editing,setEditing]=useState(false),[choice,setChoice]=useState(null),[stopping,setStopping]=useState(false),[error,setError]=useState('');
  const state=cardState(card),grant=card.grant,Rm=rm[0].toUpperCase()+rm.slice(1),off=busy||saving;
  function edit() {setEditing(true);setStopping(false);setChoice(defaultChoice(card,sharing));setError('');}
  async function save(event) {
    event.preventDefault();
    let target;
    try {target=grantTarget(card,choice,sharing);} catch(failure) {setError(failure.message);return;}
    if(await act('consent.grant',target,`Saved. ${choiceSummary(card,choice,sharing,formatDate)}`))setEditing(false);
  }
  async function stop() {
    if(await act('consent.revoke',{grant_id:grant.grant_id},`Switched off. ${Rm} no longer sees your ${card.label.toLowerCase()}.`))setStopping(false);
  }
  const toggle=()=>grant?(setStopping(true),setEditing(false)):edit();
  return <div className="sharing-detail" data-testid={`consent-card-${card.code}`} data-on={state.on}>
    <h1>{card.label}</h1>
    {status}
    <p className="sharing-lead">{card.description}</p>
    {asks.map(ask=><div key={ask.ask_id} className="sharing-ask" data-testid="consent-ask"><p>{askLine(ask,formatDateTime)}</p></div>)}
    <div className="sharing-switch">
      <div><strong>{state.switchable?`Sharing with ${Rm}`:state.label}</strong>
        <small data-testid="consent-card-scope">{grant?.basis==='notice'?'Your choice on each meeting: before the call you say whether a transcript may be used for the minutes, and you can withdraw it on that meeting at any time (My requests, Meetings). Approved minutes stay.':grant?[scopeText(card.code,grant.scope,sharing.accounts),untilText(grant,grant.until==='date'?formatDate:formatDateTime)].filter(Boolean).join(' · '):
          state.switchable?`${Rm} can't see this.`:card.reason||''}</small></div>
      {state.switchable&&sharing.rm&&<button type="button" role="switch" aria-checked={!!grant} aria-label={`Share ${card.label.toLowerCase()} with ${Rm}`}
        className="sharing-toggle" data-testid={grant?'consent-card-stop':'consent-card-edit'} disabled={off} onClick={toggle}><span/></button>}
    </div>
    {grant?.renewal_due&&<p className="page-note" data-testid="consent-card-renewal">This ends soon. Switch it on again if you want {rm} to keep seeing it.</p>}
    {state.switchable&&sharing.rm&&grant&&!editing&&!stopping&&<button type="button" className="sharing-link" data-testid="consent-card-change" disabled={off} onClick={edit}>Change what you share</button>}
    {error&&<p role="alert" className="sharing-alert">{error}</p>}
    {stopping&&grant&&<div className="sharing-confirm" data-testid="consent-card-consequence">
      <p>{revokeConsequence(card.code,Rm)}</p>
      <div className="panel-actions">
        <button type="button" className="primary-button" data-testid="consent-card-stop-confirm" disabled={off} onClick={stop}>Switch off now</button>
        <button type="button" className="bank-chat-logout" disabled={saving} onClick={()=>setStopping(false)}>Keep sharing</button>
      </div></div>}
    {editing&&choice&&<form className="sharing-confirm" data-testid="consent-card-form" onSubmit={save}>
      <fieldset disabled={off}>
        {card.code==='transaction_insights'?<>
          <legend>Which accounts</legend>
          <label><input type="radio" name="accounts" checked={choice.accounts==='all'} onChange={()=>setChoice({...choice,accounts:'all'})}/>All my accounts and cards</label>
          <label><input type="radio" name="accounts" checked={choice.accounts!=='all'} onChange={()=>setChoice({...choice,accounts:sharing.accounts.slice(0,1).map(a=>a.account_id)})}/>Only the accounts I choose (no card spending)</label>
          {choice.accounts!=='all'&&sharing.accounts.map(a=><label key={a.account_id} className="sharing-indent"><input type="checkbox" data-testid="consent-card-account" checked={choice.accounts.includes(a.account_id)}
            onChange={event=>setChoice({...choice,accounts:event.target.checked?[...choice.accounts,a.account_id]:choice.accounts.filter(n=>n!==a.account_id)})}/>{accountName(a)}</label>)}
          <label className="sharing-field">How far back <select data-testid="consent-card-lookback" value={choice.lookback_months} onChange={event=>setChoice({...choice,lookback_months:Number(event.target.value)})}>
            {LOOKBACK.map(n=><option key={n} value={n}>Last {n} months</option>)}</select></label>
        </>:<>
          <legend>Which topic areas</legend>
          {Object.entries(TOPIC_AREAS).map(([code,label])=><label key={code}><input type="checkbox" data-testid="consent-card-area" checked={choice.topic_areas.includes(code)}
            onChange={event=>setChoice({...choice,topic_areas:event.target.checked?[...choice.topic_areas,code]:choice.topic_areas.filter(a=>a!==code)})}/>{label}</label>)}
        </>}
        <label className="sharing-field">Until <select data-testid="consent-card-until" value={choice.until} onChange={event=>setChoice({...choice,until:event.target.value})}>
          <option value="date">A date</option>{sharing.next_meeting&&<option value="meeting">My meeting on {formatDateTime(sharing.next_meeting.starts_at)} ends</option>}</select></label>
        {choice.until==='date'&&<label className="sharing-field">End date <input type="date" data-testid="consent-card-date" min={plusDays(sharing.today,1)} max={latestEnd(sharing.today,card.max_months||12)}
          value={choice.expires_on} onChange={event=>setChoice({...choice,expires_on:event.target.value})}/></label>}
      </fieldset>
      <p className="page-note">Nothing is shared until you confirm. You can switch it off here at any time.</p>
      <div className="panel-actions"><button type="submit" className="primary-button" data-testid="consent-card-save" disabled={off}>Confirm</button>
        <button type="button" className="bank-chat-logout" disabled={saving} onClick={()=>setEditing(false)}>Cancel</button></div>
    </form>}
    {card.code==='chat_topics'&&(topics?<TalkingPoints data={topics} rm={rm} busy={busy} saving={saving} act={act}/>
      :topicsError&&<p role="alert" className="sharing-alert" data-testid="talking-points-error">{topicsError}</p>)}
  </div>;
}
