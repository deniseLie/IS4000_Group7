import {useEffect,useRef,useState} from 'react';
import Panel from './Panel';
import {supabase} from '../lib/supabase';
import {confirmAction,loadActions,rejectAction} from '../lib/actions';
import {resumeWorkflow} from '../lib/workflows';
import {appointmentTime} from '../lib/appointmentData';
import {consentCategoryText} from '../lib/consentData';

function commandTitle(command) {
  if(command.type==='card.temporary_block')return `Temporarily block synthetic card ending ${command.target.last4}`;
  if(command.type==='card.unblock')return `Staff reactivation of synthetic card ending ${command.target.last4}`;
  if(command.type==='card.cancel_permanently')return `Staff cancellation of synthetic card ending ${command.target.last4}`;
  if(command.type==='handoff.claim')return 'Staff claimed a demo support handoff';
  if(command.type==='handoff.close')return 'Staff closed a demo support handoff';
  if(command.type==='handoff.create')return 'Record a demo support handoff';
  if(command.type==='appointment.hold')return 'Hold a synthetic appointment';
  if(command.type==='appointment.confirm')return 'Confirm a synthetic appointment';
  if(command.type==='appointment.cancel')return 'Cancel a synthetic appointment';
  if(command.type==='consent.request')return 'Record a data-sharing consent request';
  if(command.type==='consent.grant')return 'Grant reviewed data-sharing consent';
  if(command.type==='consent.revoke')return 'Revoke a recorded data-sharing grant';
  if(command.type==='compliance.update_checklist_item')return 'Save a synthetic checklist preparation mark';
  if(command.type==='agenda.save')return 'Save a private synthetic agenda draft';
  if(command.type==='rm.share_summary')return 'Share a saved synthetic agenda snapshot';
  throw new Error('This action type has no review title. Reload its recorded status.');
}

function receiptText(item) {
  if(item.receipt.outcome==='failed'&&item.command.type==='rm.share_summary')return 'This snapshot was not shared. The saved draft, assigned recipient or reviewed consent changed. Reopen Data sharing before proposing another snapshot.';
  if(item.receipt.outcome==='failed')return 'This command did not change the target. Its state changed before execution.';
  if(item.command.type==='card.temporary_block')return 'This command temporarily blocked the synthetic card.';
  if(item.command.type==='card.unblock')return 'A verified demo support session reactivated this synthetic card.';
  if(item.command.type==='card.cancel_permanently')return 'A verified demo support session permanently cancelled this synthetic card.';
  if(item.command.type==='handoff.claim')return 'A verified demo support session claimed this synthetic support case.';
  if(item.command.type==='handoff.close')return 'A verified demo support session closed this synthetic support case. This does not change a card or reverse a transaction.';
  if(item.command.type==='handoff.create')return 'A support handoff is recorded in the demo. There is no staffed support queue.';
  if(item.command.type==='appointment.hold')return 'This command recorded a ten-minute synthetic appointment hold. Check current appointments before confirming.';
  if(item.command.type==='appointment.confirm')return 'This command confirmed the synthetic appointment. Check current appointments for its latest status. No real meeting or joining link is connected.';
  if(item.command.type==='appointment.cancel')return 'This command cancelled the synthetic appointment.';
  if(item.command.type==='consent.request')return 'This command recorded a consent request. It did not grant consent or share data.';
  if(item.command.type==='consent.grant')return 'This command granted the reviewed consent scope. Check Data sharing for its current status. Granting consent does not itself share data.';
  if(item.command.type==='consent.revoke')return 'This command revoked the reviewed grant. Other active grants remain until separately revoked or expired. Previously shared records are not erased.';
  if(item.command.type==='compliance.update_checklist_item')return 'This command saved your preparation mark only. It did not submit or verify a document or change the bank review status. Reload Records for the current mark.';
  if(item.command.type==='agenda.save')return 'This command saved a private agenda draft. It did not share data. Check Data sharing for the current draft version.';
  if(item.command.type==='rm.share_summary')return 'This command recorded the reviewed snapshot for the synthetic relationship manager. Later edits or consent revocation do not erase that shared copy. No real advisor was contacted.';
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
        {command.type==='compliance.update_checklist_item'&&<div data-testid="command-checklist"><p>{command.target.case_title} · {command.target.item_label}</p><p>Preparation: {command.target.expected_client_mark.replaceAll('_',' ')} → {command.target.client_mark.replaceAll('_',' ')}. Recorded bank status: {command.target.submission_status.replaceAll('_',' ')}.</p></div>}
        {command.type==='agenda.save'&&<div data-testid="command-agenda"><p>Draft recipient: {command.target.recipient_name}. Intended draft revision: {command.target.expected_revision+1}.</p><pre className="agenda-content">{command.target.content}</pre></div>}
        {command.type==='rm.share_summary'&&<div data-testid="command-agenda-sharing"><p>Recipient: {command.target.recipient_name}. Saved agenda revision: {command.target.agenda_revision}.</p>
          <p>Purpose: relationship-manager briefing. Data included: {consentCategoryText(command.target.categories)}.</p>
          <p>Consent grant: <code>{command.target.consent_grant_id}</code>. Grant covers: {consentCategoryText(command.target.consent_categories)}. {command.target.grant_expires_at?`Expires ${new Date(command.target.grant_expires_at).toLocaleString()}.`:'This grant has no expiry.'}</p>
          <pre className="agenda-content">{command.target.content}</pre>{receipt?.outcome==='succeeded'&&<p>Shared at {new Date(receipt.result.shared_at).toLocaleString()}. Snapshot: <code>{receipt.result.summary_id}</code>.</p>}</div>}
        {command.type.startsWith('appointment.')&&<p data-testid="command-appointment">{appointmentTime(command.target.starts_at)} – {appointmentTime(command.target.ends_at)} (Singapore time). Format: {command.target.format.replaceAll('_',' ')}.</p>}
        {command.type.startsWith('consent.')&&<div data-testid="command-consent-scope"><p>Recipient: {command.target.recipient_name||command.target.recipient}</p><p>Purpose: {command.target.purpose}. Data categories: {consentCategoryText(command.target.categories)}.</p><p>{command.type==='consent.request'?`Requested duration: ${command.target.duration_days} days.`:command.target.expires_at?`Grant expires: ${new Date(command.target.expires_at).toLocaleString()}.`:'This recorded grant has no expiry.'}</p></div>}
        <p>Recorded status: <strong data-testid="command-state">{command.state}</strong></p>
        {item.workflow&&<div data-testid="command-workflow">
          <p>Saved reply: {item.workflow.state.replaceAll('_',' ')}. {item.workflow.state==='completed'?'Close Actions to read the saved reply.':item.workflow.state==='failed'?'The action decision is recorded, but the reply could not resume. Retry the saved reply.':!item.workflow.ready?'The review is still being saved. Reload its recorded status before deciding.':''}</p>
          {item.workflow.can_resume&&<button className="bank-chat-logout" data-testid="resume-workflow" disabled={busy||!!error} onClick={()=>retry(item.workflow.turn_id)}>Resume saved reply</button>}
        </div>}
        {command.state==='proposed'&&<>
          <p>Proposal expires: <time dateTime={command.expires_at}>{new Date(command.expires_at).toLocaleString()}</time>. The server checks expiry before executing.</p>
          {!chosen&&<button className="bank-chat-logout" data-testid="review-command" disabled={busy||!!error||item.workflow&&!item.workflow.ready} onClick={()=>{setSelected(command.command_id);setAcknowledged(false);}}>Review proposal</button>}
          {chosen&&<div className="command-review" data-testid="command-review">
            <p>{command.type==='rm.share_summary'?'Confirming shares exactly this saved snapshot with the displayed synthetic recipient. Consent and the saved revision are rechecked. Rejecting shares nothing.':command.type==='consent.grant'?'Confirming grants only the recipient, purpose, categories and expiry displayed above. This does not share data.':command.type==='consent.revoke'?'Confirming revokes this specific grant. Other grants continue until separately revoked or expired, and earlier sharing is not erased.':command.type==='appointment.confirm'?'Confirming books this synthetic time only while its hold remains active. Review the date, time and format above. No real meeting or joining link is connected.':command.type==='appointment.cancel'?'Confirming cancels this synthetic appointment and releases its time.':'Confirming temporarily blocks this synthetic card. Rejecting leaves it unchanged. This does not reverse a transaction or permanently cancel a card.'}</p>
            <label><input type="checkbox" data-testid="acknowledge-command" checked={acknowledged} disabled={busy||!!error} onChange={event=>setAcknowledged(event.target.checked)}/> I have reviewed this {command.type==='rm.share_summary'?'saved agenda snapshot and consent':command.type.startsWith('consent.')?'data-sharing scope':command.type.startsWith('appointment.')?'synthetic appointment':'synthetic card'} and the proposed change.</label>
            <div className="panel-actions"><button className="primary-button" data-testid="confirm-command" disabled={busy||!!error||!acknowledged} onClick={()=>submit(confirmAction,command.command_id)}>{command.type==='rm.share_summary'?'Confirm sharing':command.type==='consent.grant'?'Confirm consent grant':command.type==='consent.revoke'?'Confirm revocation':command.type==='appointment.confirm'?'Confirm booking':command.type==='appointment.cancel'?'Confirm cancellation':'Confirm temporary block'}</button>
            <button className="bank-chat-logout" data-testid="reject-command" disabled={busy||!!error} onClick={()=>submit(rejectAction,command.command_id)}>Reject proposal</button></div>
          </div>}
        </>}
        {command.state==='rejected'&&<p>This proposal was rejected. No proposed change was executed.</p>}
        {command.state==='expired'&&<p>This proposal expired without execution. A new proposal is required.</p>}
        {receipt&&<div data-testid="command-receipt">
          <p>{receiptText(item)}</p>
          <p>Receipt: <code>{receipt.receipt_id}</code></p>
        </div>}
      </li>;
    })}</ul>
  </Panel>;
}
