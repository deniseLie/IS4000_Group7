import {useEffect,useRef,useState} from 'react';
import {supabase} from '../lib/supabase';
import {createActionCase,proposeAction} from '../lib/actions';

export default function ChecklistPreparation({review,item,onSaved}) {
  const [mark,setMark]=useState(item.client_mark);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [outcome,setOutcome]=useState(null);
  const active=useRef(null);
  useEffect(()=>()=>active.current?.abort(),[]);
  async function save(event) {
    event.preventDefault();
    const controller=new AbortController();active.current=controller;setBusy(true);setError('');
    try {
      const {data,error:failure}=await supabase.auth.getSession();
      if(failure)throw failure;
      if(!data.session)throw new Error('Please sign in again to save checklist preparation.');
      const token=data.session.access_token;
      const scope=await createActionCase({session_id:crypto.randomUUID(),case_id:crypto.randomUUID()},token,controller.signal);
      const result=await proposeAction({command_id:crypto.randomUUID(),case_id:scope.case_id,task_id:crypto.randomUUID(),
        type:'compliance.update_checklist_item',target:{case_id:review.case_id,item_id:item.item_id,client_mark:mark,
          expected_client_mark:item.client_mark,expected_client_marked_at:item.client_marked_at}},token,controller.signal);
      if(!result.receipt)throw new Error('The preparation command has no recorded receipt. Check Actions before retrying.');
      if(!controller.signal.aborted)setOutcome(result);
      if(!controller.signal.aborted&&result.receipt.outcome==='succeeded'&&onSaved)onSaved();
    } catch(failure) {if(failure.name!=='AbortError'&&!controller.signal.aborted)setError(failure.message);}
    finally {if(!controller.signal.aborted)setBusy(false);}
  }
  return <div className="relationship-details" data-testid="checklist-preparation">
    {['completed','closed'].includes(review.status)?<p>This recorded review is closed to preparation changes.</p>:<form onSubmit={save}>
      <label>Your preparation mark <select data-testid="checklist-mark" value={mark} disabled={busy||!!error||!!outcome} onChange={event=>setMark(event.target.value)}>
        <option value="not_prepared">Not prepared</option><option value="prepared">Prepared</option></select></label>
      <p>Saving changes only your preparation mark for <strong>{item.label}</strong>. It does not upload or submit a document, change its bank review status, or complete this case.</p>
      <button className="bank-chat-logout" data-testid="save-checklist-mark" disabled={busy||!!error||!!outcome||mark===item.client_mark}>Save preparation mark</button>
    </form>}
    {busy&&<p role="status">Saving preparation… Closing this panel does not cancel a submitted change. Check Actions for its receipt.</p>}
    {error&&<p role="alert" data-testid="checklist-error">{error} Reload records and check Actions before retrying.</p>}
    {outcome&&<div data-testid="checklist-outcome"><p>{outcome.receipt.outcome==='succeeded'?`Recorded preparation: ${outcome.receipt.result.client_mark.replaceAll('_',' ')}.`:'The preparation mark was not saved because the checklist changed.'} Reload records for its current state.</p>
      <p>Receipt: <code>{outcome.receipt.receipt_id}</code></p></div>}
  </div>;
}
