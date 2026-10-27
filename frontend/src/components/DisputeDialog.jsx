import {useEffect,useRef,useState} from 'react';
import Panel from './Panel';
import {confirmAction,loadAction,rejectAction} from '../lib/actions';
import {POSSESSION} from '../lib/disputeData';
import {supabase} from '../lib/supabase';
import {formatDate,formatDateTime,formatMoney} from '../lib/format';

async function token() {
  const {data,error}=await supabase.auth.getSession();
  if(error)throw error;
  if(!data.session)throw new Error('Please sign in again to decide on the dispute draft.');
  return data.session.access_token;
}

// In-chat review of a proposed dispute draft. The charges come from the stored command; the card answer is the only input.
export default function DisputeDialog({action,onClose,onDecided}) {
  const [command,setCommand]=useState(null);
  const [answer,setAnswer]=useState('');
  const [busy,setBusy]=useState(true);
  const [error,setError]=useState('');
  const [outcome,setOutcome]=useState(null);
  const active=useRef(null);
  useEffect(()=>{
    const controller=new AbortController();active.current=controller;
    (async()=>{
      try {const saved=await loadAction(action.command_id,await token(),controller.signal);if(!controller.signal.aborted)setCommand(saved.command);}
      catch(failure) {if(failure.name!=='AbortError'&&!controller.signal.aborted)setError(failure.message);}
      finally {if(!controller.signal.aborted)setBusy(false);}
    })();
    return ()=>active.current?.abort();
  },[action.command_id]);
  async function decide(submit) {
    const controller=new AbortController();active.current=controller;setBusy(true);setError('');
    try {
      const saved=await submit(action.command_id,await token(),controller.signal);
      if(!controller.signal.aborted){setOutcome(saved);onDecided(saved);}
    } catch(failure) {if(failure.name!=='AbortError'&&!controller.signal.aborted)setError(failure.message);}
    finally {if(!controller.signal.aborted)setBusy(false);}
  }
  const target=command?.target,last4=target?.last4||action.summary.match(/\d{4}$/)?.[0]||'';
  const state=outcome?.command.state;
  const result=outcome?.receipt?.outcome==='succeeded'?`Your dispute draft for ${target.items.length} charges is recorded for bank staff review. Nothing has been refunded.`
    :state==='rejected'?'You chose not to send the dispute draft. Nothing was sent.':state==='expired'?'The dispute draft expired without being sent.'
    :'The dispute draft was not recorded because a listed charge changed. Nothing was sent.';
  return <Panel label="Dispute draft" closeLabel="Close dispute review" title={`Send a dispute draft for card ending ${last4}?`} onClose={onClose} locked={busy&&!!command}>
    <div data-testid="dispute-dialog">
      {!outcome&&target&&<>
        <p>These charges on your synthetic card ending <strong>{last4}</strong> go to bank staff, who decide any refund or cancellation. Nothing is refunded now.</p>
        <ul data-testid="dispute-charges">{target.items.map(item=><li key={item.transaction_id}>{formatDate(item.date)} · {item.merchant} · {formatMoney(item.amount,item.currency)}</li>)}</ul>
        <fieldset className="dispute-answer"><legend>Do you still have the card?</legend>
          {POSSESSION.map(([key,label])=><label key={key}><input type="radio" name="dispute-possession" data-testid={`dispute-answer-${key}`} value={key} checked={answer===key} disabled={busy} onChange={()=>setAnswer(key)}/> {label}</label>)}</fieldset>
        <p>Declining sends nothing. This offer expires at <time dateTime={action.expires_at}>{formatDateTime(action.expires_at)}</time>.</p>
        <div className="panel-actions"><button className="primary-button" data-testid="confirm-dispute" disabled={busy||!answer}
          onClick={()=>decide((id,key,signal)=>confirmAction(id,key,signal,{card_in_possession:answer}))}>Send dispute draft</button>
          <button className="bank-chat-logout" data-testid="decline-dispute" disabled={busy} onClick={()=>decide(rejectAction)}>Decline</button></div>
      </>}
      {busy&&<p className="loading" role="status">{command?'Recording your decision…':'Loading the dispute draft…'}</p>}
      {error&&<p role="alert">{error} Reload the chat to see what happened.</p>}
      {outcome&&<div data-testid="dispute-outcome" role="status">
        <p>{result}</p>
        {outcome.receipt&&<p data-testid="dispute-receipt">Receipt: <code>{outcome.receipt.receipt_id}</code></p>}
        <div className="panel-actions"><button className="primary-button" onClick={onClose}>Back to chat</button></div>
      </div>}
    </div>
  </Panel>;
}
