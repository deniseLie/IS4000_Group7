import {useRef,useState} from 'react';
import Icon from '../Icon';
import RmAnswer from './RmAnswer';
import {askAssistant,requestContext} from '../../lib/rm';

// Follow up step (design: FollowUp): the financing specialist referral and the compliance question are drafted right
// here from the approved minutes by the RM Requests agent (intent document_interaction). Each request form is editable
// and is sent only when she approves it (the same review cards as in the assistant). "Redo" asks again with her note.
export default function HandoffDrafts({token,notes,clientId,meetingId,client}) {
  const [reply,setReply]=useState(null),[busy,setBusy]=useState(false),[status,setStatus]=useState(''),[error,setError]=useState(''),[redo,setRedo]=useState('');
  const request=useRef(null);
  async function draft(extra='') {
    request.current?.abort();const control=new AbortController();request.current=control;
    setBusy(true);setError('');setStatus('Drafting from the approved minutes…');
    try {
      const message=`Document this meeting${client?` with ${client}`:''}. Prepare the financing specialist referral and any compliance review question it needs.${extra?` ${extra}`:''}\n${notes}`;
      const answer=await askAssistant({message,history:[],context:requestContext({client_id:clientId,appointment_id:meetingId}),intent:'document_interaction'},await token(),setStatus,control.signal);
      if(!control.signal.aborted){setReply(answer);setRedo('');}
    } catch(failure) {if(failure.name!=='AbortError')setError(failure.message);}
    finally {if(!control.signal.aborted){setBusy(false);setStatus('');}}
  }
  return <section className="rmw-card meeting-draft-card meeting-handoffs" aria-label="Hand-offs inside the bank" data-testid="meeting-handoffs">
    <div className="meeting-draft-head"><h3>Hand-offs inside the bank</h3><span className="tag warn">{reply?'Draft':'Not drafted yet'}</span></div>
    <p className="rm-small">Financing specialist referral and compliance review question, drafted from the approved minutes. Edit each form; each is sent to its team only when you approve it.</p>
    {error&&<p role="alert" className="rm-error">{error}</p>}
    {!reply&&<button type="button" className="bank-chat-logout" data-testid="meeting-draft-handoffs" disabled={busy||!notes} onClick={()=>draft()}><Icon name="spark"/>{busy?status||'Drafting…':'Draft the hand-offs'}</button>}
    {reply&&<>
      <RmAnswer reply={reply} token={token} latest client={client} onEdit={setReply} onDocumentationChange={()=>{}} onDraftChange={()=>{}}/>
      <form className="meeting-redo" onSubmit={e=>{e.preventDefault();draft(redo.trim());}}>
        <label><span className="vh">Ask for a redo</span><input className="in" value={redo} maxLength={300} disabled={busy} placeholder="Not right? e.g. “the timeline is within 12 months”" onChange={e=>setRedo(e.target.value)}/></label>
        <button type="submit" className="bank-chat-logout" disabled={busy}>{busy?'Redoing…':'Redo hand-offs'}</button></form></>}
  </section>;
}
