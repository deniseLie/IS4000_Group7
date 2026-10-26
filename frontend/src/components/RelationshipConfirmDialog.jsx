import {useState} from 'react';
import Panel from './Panel';

// In-chat review of one stored relationship proposal. Nothing changes until the customer confirms; the parent shows
// the result only from the server receipt. Closing or declining sends nothing further.
export default function RelationshipConfirmDialog({label,title,subtitle,confirmLabel,declineLabel='Cancel',acknowledgement,onConfirm,onDecline,onClose,children}) {
  const [agreed,setAgreed]=useState(!acknowledgement);
  const [busy,setBusy]=useState(false);
  const [status,setStatus]=useState('');
  const [error,setError]=useState('');
  async function run(operation) {
    setBusy(true);setError('');
    try {await operation(setStatus);}
    catch(failure) {if(failure.name!=='AbortError'){setError(failure.message);setBusy(false);}}
  }
  return <Panel label={label} closeLabel={`Close ${label.toLowerCase()}`} title={title} subtitle={subtitle} onClose={busy?()=>{}:onClose}
    footer={<><span>Recorded only after the system confirms it</span><div className="panel-actions">
      <button className="bank-chat-logout" data-testid="rm-dialog-decline" disabled={busy} onClick={()=>onDecline?run(onDecline):onClose()}>{declineLabel}</button>
      <button className="primary-button" data-testid="rm-dialog-confirm" disabled={busy||!agreed} onClick={()=>run(onConfirm)}>{confirmLabel}</button></div></>}>
    <div className="rm-dialog" data-testid="rm-dialog">
      {children}
      {acknowledgement&&<label className="review-summary rm-consent"><input type="checkbox" data-testid="rm-dialog-acknowledge" checked={agreed} disabled={busy} onChange={event=>setAgreed(event.target.checked)}/>{acknowledgement}</label>}
      {busy&&<p role="status" data-testid="rm-dialog-status">{status||'Recording your decision…'} Closing the page does not cancel a submitted step.</p>}
      {error&&<p role="alert" data-testid="rm-dialog-error">{error}</p>}
    </div>
  </Panel>;
}
