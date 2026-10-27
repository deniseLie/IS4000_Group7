import {useEffect,useRef,useState} from 'react';
import Panel from './Panel';
import {confirmAction,rejectAction} from '../lib/actions';
import {supabase} from '../lib/supabase';
import {formatDateTime} from '../lib/format';

// In-chat review of a proposed temporary block. The server confirms or rejects the stored command; closing sends nothing.
export default function CardBlockDialog({action,onClose,onDecided}) {
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [outcome,setOutcome]=useState(null);
  const active=useRef(null);
  useEffect(()=>()=>active.current?.abort(),[]);
  const last4=action.summary.match(/\d{4}$/)?.[0]||'';
  async function decide(submit) {
    const controller=new AbortController();active.current=controller;setBusy(true);setError('');
    try {
      const {data,error:sessionError}=await supabase.auth.getSession();
      if(sessionError)throw sessionError;
      if(!data.session)throw new Error('Please sign in again to decide on the card block.');
      const saved=await submit(action.command_id,data.session.access_token,controller.signal);
      if(!controller.signal.aborted){setOutcome(saved);onDecided(saved);}
    } catch(failure) {if(failure.name!=='AbortError'&&!controller.signal.aborted)setError(failure.message);}
    finally {if(!controller.signal.aborted)setBusy(false);}
  }
  const state=outcome?.command.state;
  const result=outcome?.receipt?.outcome==='succeeded'?`Your card ending ${last4} is temporarily blocked.`:state==='rejected'?`You chose not to block the card ending ${last4}; it stays active.`
    :state==='expired'?'The proposal expired without changing your card.':'The temporary block failed. Your card was not changed.';
  return <Panel label="Temporary card block" closeLabel="Close card block review" title={`Block card ending ${last4}?`} onClose={onClose}>
    <div data-testid="card-block-dialog">
      {!outcome&&<>
        <p>Confirming temporarily blocks your synthetic card ending <strong>{last4}</strong>, so new card payments stop. It does not reverse any transaction. Identity verification and permanent cancellation are done by bank staff.</p>
        <p>Declining leaves the card active. This offer expires at <time dateTime={action.expires_at}>{formatDateTime(action.expires_at)}</time>.</p>
        <div className="panel-actions"><button className="primary-button" data-testid="confirm-card-block" disabled={busy} onClick={()=>decide(confirmAction)}>Confirm</button>
          <button className="bank-chat-logout" data-testid="decline-card-block" disabled={busy} onClick={()=>decide(rejectAction)}>Decline</button></div>
      </>}
      {busy&&<p className="loading" role="status">Recording your decision…</p>}
      {error&&<p role="alert">{error} Reload the saved chat to check the recorded status.</p>}
      {outcome&&<div data-testid="card-block-outcome" role="status">
        <p>{result}</p>
        {outcome.receipt&&<p data-testid="card-block-receipt">Receipt: <code>{outcome.receipt.receipt_id}</code></p>}
        <div className="panel-actions"><button className="primary-button" onClick={onClose}>Back to chat</button></div>
      </div>}
    </div>
  </Panel>;
}
