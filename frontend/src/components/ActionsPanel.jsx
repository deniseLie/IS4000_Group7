import {useEffect,useRef,useState} from 'react';
import Panel from './Panel';
import {supabase} from '../lib/supabase';
import {confirmAction,loadActions,rejectAction} from '../lib/actions';
import {resumeWorkflow} from '../lib/workflows';

function commandTitle(command) {
  if(command.type==='card.temporary_block')return `Temporarily block synthetic card ending ${command.target.last4}`;
  if(command.type==='card.unblock')return `Staff reactivation of synthetic card ending ${command.target.last4}`;
  if(command.type==='card.cancel_permanently')return `Staff cancellation of synthetic card ending ${command.target.last4}`;
  if(command.type==='handoff.claim')return 'Staff claimed a demo support handoff';
  if(command.type==='handoff.close')return 'Staff closed a demo support handoff';
  if(command.type==='handoff.create')return 'Record a demo support handoff';
  throw new Error('This action type has no review title. Reload its recorded status.');
}

function receiptText(item) {
  if(item.receipt.outcome==='failed')return 'This command did not change the target. Its state changed before execution.';
  if(item.command.type==='card.temporary_block')return 'This command temporarily blocked the synthetic card.';
  if(item.command.type==='card.unblock')return 'A verified demo support session reactivated this synthetic card.';
  if(item.command.type==='card.cancel_permanently')return 'A verified demo support session permanently cancelled this synthetic card.';
  if(item.command.type==='handoff.claim')return 'A verified demo support session claimed this synthetic support case.';
  if(item.command.type==='handoff.close')return 'A verified demo support session closed this synthetic support case. This does not change a card or reverse a transaction.';
  if(item.command.type==='handoff.create')return 'A support handoff is recorded in the demo. There is no staffed support queue.';
  throw new Error('This action type has no verified receipt description.');
}

export default function ActionsPanel({onClose,onWorkflowChange}) {
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
      if(!controller.signal.aborted){
        setItems(previous=>previous.map(item=>item.command.command_id===commandId?result:item));setSelected(null);setAcknowledged(false);
        if(result.workflow)onWorkflowChange(result.workflow);
        const recorded=await loadActions(await token(),controller.signal);
        if(!controller.signal.aborted)setItems(recorded);
      }
    } catch(failure) {if(failure.name!=='AbortError'&&!controller.signal.aborted)setError(failure.message);}
    finally {if(!controller.signal.aborted)setBusy(false);}
  }
  async function retry(turnId) {
    const controller=new AbortController();active.current=controller;setBusy(true);setError('');
    try {
      const workflow=await resumeWorkflow(turnId,await token(),controller.signal);
      const recorded=await loadActions(await token(),controller.signal);
      if(!controller.signal.aborted){onWorkflowChange(workflow);setItems(recorded);}
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
        <h3>{commandTitle(command)}</h3>
        {command.type==='handoff.create'&&<p>{command.target.summary}</p>}
        <p>Recorded status: <strong data-testid="command-state">{command.state}</strong></p>
        {item.workflow&&<div data-testid="command-workflow">
          <p>Saved reply: {item.workflow.state.replaceAll('_',' ')}. {item.workflow.state==='completed'?'Close Actions to read the saved reply.':item.workflow.state==='failed'?'The action decision is recorded, but the reply could not resume. Retry the saved reply.':!item.workflow.ready?'The review is still being saved. Reload its recorded status before deciding.':''}</p>
          {item.workflow.can_resume&&<button className="bank-chat-logout" data-testid="resume-workflow" disabled={busy||!!error} onClick={()=>retry(item.workflow.turn_id)}>Resume saved reply</button>}
        </div>}
        {command.state==='proposed'&&<>
          <p>Proposal expires: <time dateTime={command.expires_at}>{new Date(command.expires_at).toLocaleString()}</time>. The server checks expiry before executing.</p>
          {!chosen&&<button className="bank-chat-logout" data-testid="review-command" disabled={busy||!!error||item.workflow&&!item.workflow.ready} onClick={()=>{setSelected(command.command_id);setAcknowledged(false);}}>Review proposal</button>}
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
          <p>{receiptText(item)}</p>
          <p>Receipt: <code>{receipt.receipt_id}</code></p>
        </div>}
      </li>;
    })}</ul>
  </Panel>;
}
