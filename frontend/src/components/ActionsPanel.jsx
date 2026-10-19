import {useEffect,useRef,useState} from 'react';
import Panel from './Panel';
import {supabase} from '../lib/supabase';
import {confirmAction,loadActions,rejectAction} from '../lib/actions';

export default function ActionsPanel({onClose}) {
  const [items,setItems]=useState(null);
  const [selected,setSelected]=useState(null);
  const [acknowledged,setAcknowledged]=useState(false);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const active=useRef(null);
  useEffect(()=>{
    const controller=new AbortController();active.current=controller;
    refresh(controller);
    return ()=>active.current?.abort();
  },[]);
  async function token() {
    const {data,error:failure}=await supabase.auth.getSession();
    if(failure)throw failure;
    if(!data.session)throw new Error('Please sign in again to review actions.');
    return data.session.access_token;
  }
  async function refresh(controller) {
    setBusy(true);setError('');setSelected(null);setAcknowledged(false);
    try {
      const result=await loadActions(await token(),controller.signal);
      if(!controller.signal.aborted)setItems(result);
    } catch(failure) {
      if(failure.name!=='AbortError'&&!controller.signal.aborted){setError(failure.message);setItems(null);}
    } finally {if(!controller.signal.aborted)setBusy(false);}
  }
  async function submit(operation,commandId) {
    const controller=new AbortController();active.current=controller;
    setBusy(true);setError('');
    try {
      const result=await operation(commandId,await token(),controller.signal);
      if(!controller.signal.aborted){setItems(previous=>previous.map(item=>item.command.command_id===commandId?result:item));setSelected(null);setAcknowledged(false);}
    } catch(failure) {if(failure.name!=='AbortError'&&!controller.signal.aborted)setError(failure.message);}
    finally {if(!controller.signal.aborted)setBusy(false);}
  }
  return <Panel label="Actions" title="Review proposed actions" closeLabel="Close actions" onClose={onClose}>
    <p>Review proposed changes before approving them. These actions affect synthetic demo records only; no real bank card is affected.</p>
    <button data-testid="reload-actions" className="bank-chat-logout" disabled={busy} onClick={()=>{const controller=new AbortController();active.current=controller;refresh(controller);}}>Reload recorded status</button>
    {busy&&<p role="status">Checking the recorded result… Closing this panel does not cancel a submitted decision. Reopen Actions to check its status.</p>}
    {error&&<p role="alert" data-testid="actions-error">{error} Reload the recorded status before making another decision.</p>}
    {items?.length===0&&<p data-testid="actions-empty">No actions have been proposed for your demo profile.</p>}
    <ul className="command-list" data-testid="commands-list">{items?.map(item=>{
      const command=item.command,receipt=item.receipt,chosen=selected===command.command_id;
      return <li key={command.command_id} data-command-id={command.command_id}>
        <h3>{command.type==='card.temporary_block'?`Temporarily block synthetic card ending ${command.target.last4}`:'Record a demo support handoff'}</h3>
        {command.type==='handoff.create'&&<p>{command.target.summary}</p>}
        <p>Recorded status: <strong data-testid="command-state">{command.state}</strong></p>
        {command.state==='proposed'&&<>
          <p>Proposal expires: <time dateTime={command.expires_at}>{new Date(command.expires_at).toLocaleString()}</time>. The server checks expiry before executing.</p>
          {!chosen&&<button className="bank-chat-logout" data-testid="review-command" disabled={busy||!!error} onClick={()=>{setSelected(command.command_id);setAcknowledged(false);}}>Review proposal</button>}
          {chosen&&<div className="command-review" data-testid="command-review">
            <p>Confirming temporarily blocks this synthetic card. Rejecting leaves it unchanged. This does not reverse a transaction or permanently cancel a card.</p>
            <label><input type="checkbox" data-testid="acknowledge-command" checked={acknowledged} disabled={busy||!!error} onChange={event=>setAcknowledged(event.target.checked)}/> I have reviewed this synthetic card and the proposed change.</label>
            <div className="panel-actions"><button className="primary-button" data-testid="confirm-command" disabled={busy||!!error||!acknowledged} onClick={()=>submit(confirmAction,command.command_id)}>Confirm temporary block</button>
            <button className="bank-chat-logout" data-testid="reject-command" disabled={busy||!!error} onClick={()=>submit(rejectAction,command.command_id)}>Reject proposal</button></div>
          </div>}
        </>}
        {command.state==='rejected'&&<p>This proposal was rejected. It did not change the synthetic card.</p>}
        {command.state==='expired'&&<p>This proposal expired without execution. A new proposal is required.</p>}
        {receipt&&<div data-testid="command-receipt">
          <p>{receipt.outcome==='failed'?'This command did not change the target. Its state changed before execution.':command.type==='card.temporary_block'?'This command temporarily blocked the synthetic card.':'A support handoff is recorded in the demo. There is no staffed support queue.'}</p>
          <p>Receipt: <code>{receipt.receipt_id}</code></p>
        </div>}
      </li>;
    })}</ul>
  </Panel>;
}
