import {useEffect,useRef,useState} from 'react';
import {supabase} from '../lib/supabase';
import {createActionCase,proposeAction} from '../lib/actions';
import {DOCUMENT_TYPES,fileDescriptor} from '../lib/documentUploadData';
import {prepareUpload,uploadFile} from '../lib/documentStorage';

export default function DocumentUpload({review}) {
  const [file,setFile]=useState(null),[item,setItem]=useState(''),[busy,setBusy]=useState(false);
  const [error,setError]=useState(''),[pending,setPending]=useState(null),[outcome,setOutcome]=useState(null);
  const mounted=useRef(true),active=useRef(null);
  useEffect(()=>{mounted.current=true;return ()=>{mounted.current=false;active.current?.abort();};},[]);
  async function session() {
    const {data,error:failure}=await supabase.auth.getSession();
    if(failure)throw failure;
    if(!data.session)throw new Error('Sign in again to upload a private synthetic file.');
    return data.session;
  }
  async function register(preparation,token) {
    const result=await proposeAction(preparation.command,token,undefined);
    if(!result.receipt)throw new Error('No upload receipt was returned. Check Actions or retry recording this uploaded file.');
    if(mounted.current)setOutcome(result);
  }
  async function submit(event) {
    event.preventDefault();setBusy(true);setError('');
    const controller=new AbortController();active.current=controller;
    try {
      const descriptor=fileDescriptor(file),auth=await session();
      const scope=await createActionCase({session_id:crypto.randomUUID(),case_id:crypto.randomUUID()},auth.access_token,controller.signal);
      const target={...descriptor,document_id:crypto.randomUUID(),case_id:review.case_id,item_id:item||null};
      const prepared=await prepareUpload(target,scope.customer_id,auth.access_token,controller.signal);
      controller.signal.throwIfAborted();
      const preparation={user_id:auth.user.id,command:{command_id:crypto.randomUUID(),case_id:scope.case_id,
        task_id:crypto.randomUUID(),type:'document.register',target}};
      if(mounted.current)setPending(preparation);
      // Submitted writes finish even when this panel closes; only preparation reads are aborted.
      await uploadFile(prepared,file,auth.access_token);
      await register(preparation,auth.access_token);
    } catch(failure) {if(mounted.current&&failure.name!=='AbortError')setError(failure.message);}
    finally {if(mounted.current)setBusy(false);}
  }
  async function retry() {
    setBusy(true);setError('');
    try {
      const auth=await session();
      if(auth.user.id!==pending.user_id)throw new Error('This upload belongs to the earlier sign-in. Sign in to that demo account to record it.');
      await register(pending,auth.access_token);
    } catch(failure) {if(mounted.current)setError(failure.message);}
    finally {if(mounted.current)setBusy(false);}
  }
  if(!review||['completed','closed'].includes(review.status))return <p data-testid="document-upload-unavailable">Uploading requires an open review case.</p>;
  return <div className="relationship-details" data-testid="document-upload">
    <h4>Upload a private synthetic file</h4>
    <p>PDF, DOCX, JPEG or PNG, up to 10 MiB. Uploading records a file; bank acceptance, preliminary checks and checklist submission status are separate.</p>
    <form className="records-filters" onSubmit={submit}>
      <label>Requested item <select data-testid="document-upload-item" value={item} disabled={busy||!!pending||!!outcome} onChange={event=>setItem(event.target.value)}>
        <option value="">General evidence for this review</option>{review.requested_items.map(row=><option key={row.item_id} value={row.item_id}>{row.label}</option>)}</select></label>
      <label>Synthetic file <input data-testid="document-upload-file" type="file" accept={Object.keys(DOCUMENT_TYPES).join(',')} disabled={busy||!!pending||!!outcome} onChange={event=>{setFile(event.target.files[0]||null);setError('');}}/></label>
      <button className="bank-chat-logout" data-testid="document-upload-submit" disabled={!file||busy||!!pending||!!outcome}>Upload and record file</button>
    </form>
    {busy&&<p role="status" data-testid="document-upload-busy">Uploading or recording… Keep this page open. Closing this panel does not cancel submitted writes.</p>}
    {error&&<p role="alert" data-testid="document-upload-error">{error}</p>}
    {pending&&!outcome&&!busy&&<div><p>Storage may have received the file. Recording checks that the exact owned file exists; it does not upload a second copy. Keep this panel open to retry. Closing the page can leave a private file without a record.</p>
      <button className="bank-chat-logout" data-testid="document-upload-retry" onClick={retry}>Record uploaded file</button></div>}
    {outcome&&<div data-testid="document-upload-outcome"><p>{outcome.receipt.outcome==='succeeded'?'Private file upload recorded. Reload Records to view it.':'The upload could not be recorded because its case or file changed. Check Actions and reload Records.'}</p><p>Receipt: <code>{outcome.receipt.receipt_id}</code></p></div>}
  </div>;
}
