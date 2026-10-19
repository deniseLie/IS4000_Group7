import {useEffect,useRef,useState} from 'react';
import {supabase} from '../lib/supabase';
import {createActionCase,proposeAction} from '../lib/actions';
import {loadAgendas} from '../lib/relationship';
import {AGENDA_PROFILE_FIELDS} from '../lib/agendaData';
import {consentCategoryText} from '../lib/consentData';

export default function AgendaDrafts({recipient,onReviewActions}) {
  const [registry,setRegistry]=useState(null);
  const [editor,setEditor]=useState(null);
  const [topics,setTopics]=useState('');
  const [fields,setFields]=useState([]);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [outcome,setOutcome]=useState(null);
  const active=useRef(null);
  useEffect(()=>{refresh();return ()=>active.current?.abort();},[]);
  async function token() {
    const {data,error:failure}=await supabase.auth.getSession();
    if(failure)throw failure;
    if(!data.session)throw new Error('Please sign in again to review agenda drafts.');
    return data.session.access_token;
  }
  async function refresh() {
    const controller=new AbortController();active.current=controller;setBusy(true);setError('');setRegistry(null);setEditor(null);
    try {
      const result=await loadAgendas(await token(),controller.signal);
      if(!controller.signal.aborted)setRegistry(result);
    } catch(failure) {if(failure.name!=='AbortError'&&!controller.signal.aborted)setError(failure.message);}
    finally {if(!controller.signal.aborted)setBusy(false);}
  }
  function newDraft() {
    setEditor({agenda_id:crypto.randomUUID(),advisor_id:recipient.rm_id,recipient_name:recipient.name,revision:0});
    setTopics('');setFields([]);setOutcome(null);
  }
  function editDraft(agenda) {
    setEditor(agenda);setTopics(agenda.topics.join('\n'));setFields(agenda.profile_fields);setOutcome(null);
  }
  async function save(event) {
    event.preventDefault();
    const controller=new AbortController();active.current=controller;setBusy(true);setError('');setOutcome(null);
    try {
      const access=await token();
      const scope=await createActionCase({session_id:crypto.randomUUID(),case_id:crypto.randomUUID()},access,controller.signal);
      const result=await proposeAction({command_id:crypto.randomUUID(),case_id:scope.case_id,task_id:crypto.randomUUID(),type:'agenda.save',
        target:{agenda_id:editor.agenda_id,advisor_id:editor.advisor_id,expected_revision:editor.revision,topics:topics.split('\n'),profile_fields:fields}},access,controller.signal);
      if(!controller.signal.aborted) {
        setOutcome(result);setEditor(null);
        const recorded=await loadAgendas(access,controller.signal);
        if(!controller.signal.aborted)setRegistry(recorded);
      }
    } catch(failure) {if(failure.name!=='AbortError'&&!controller.signal.aborted){setError(failure.message);setRegistry(null);setEditor(null);}}
    finally {if(!controller.signal.aborted)setBusy(false);}
  }
  const lines=topics.split('\n');
  const valid=lines.length<=12&&lines.every(line=>line.trim()&&Array.from(line).length<=300);
  return <section className="relationship-details" aria-label="Agenda drafts">
    <h3>Prepare a meeting agenda</h3>
    <p>Save topics for your synthetic relationship manager. Choose any basic profile fields to include. Saving creates a private draft and does not share data.</p>
    <button className="bank-chat-logout" data-testid="reload-agendas" disabled={busy} onClick={refresh}>Reload saved drafts</button>
    {busy&&<p role="status">Checking the saved agenda… Closing this panel does not cancel a submitted save. Reopen it or Actions to verify the result.</p>}
    {error&&<p role="alert" data-testid="agenda-error">{error} Reload saved drafts before editing again.</p>}
    {registry&&<>
      <p data-testid="agenda-count">Showing {registry.agendas.length} of {registry.agendas_total} saved drafts.</p>
      <button className="bank-chat-logout" data-testid="new-agenda" disabled={busy||!!error||!recipient} onClick={newDraft}>New agenda draft</button>
      {!recipient&&<p>A current relationship manager assignment is required to save a draft.</p>}
      {editor&&<form onSubmit={save} data-testid="agenda-editor">
        <p>Recipient for this draft: {editor.recipient_name}. Editing revision {editor.revision}.</p>
        <label htmlFor="agenda-topics">Topics, one per line (1–12 topics, 300 characters each)</label>
        <textarea id="agenda-topics" data-testid="agenda-topics" rows="5" value={topics} disabled={busy||!!error} onChange={event=>setTopics(event.target.value)}/>
        <p>Use synthetic details only. Do not enter passwords, PINs, OTPs or credentials. Recognised private details are redacted before storage; review the saved preview.</p>
        <fieldset disabled={busy||!!error}><legend>Include these recorded profile fields</legend>{Object.entries(AGENDA_PROFILE_FIELDS).map(([field,label])=>
          <label key={field}><input type="checkbox" data-testid={`agenda-field-${field}`} checked={fields.includes(field)} onChange={event=>setFields(current=>event.target.checked?[...current,field]:current.filter(value=>value!==field))}/>{label}</label>)}</fieldset>
        <button className="primary-button" data-testid="save-agenda" type="submit" disabled={busy||!!error||!valid}>Save private draft</button>
      </form>}
      <ul className="command-list" data-testid="agenda-list">{registry.agendas.map(agenda=><li key={agenda.agenda_id} data-agenda-id={agenda.agenda_id}>
        <h4>Draft for {agenda.recipient_name}</h4><p>Revision <span data-testid="agenda-revision">{agenda.revision}</span>. Categories: {consentCategoryText(agenda.categories)}.</p>
        <pre className="agenda-content" data-testid="agenda-content">{agenda.content}</pre>
        <p>Saved <time dateTime={agenda.updated_at}>{new Date(agenda.updated_at).toLocaleString()}</time>. This draft has not been shared.</p>
        <button className="bank-chat-logout" data-testid="edit-agenda" disabled={busy||!!error} onClick={()=>editDraft(agenda)}>Edit saved draft</button>
      </li>)}</ul>
    </>}
    {outcome&&<div data-testid="agenda-save-result"><p>{outcome.receipt.outcome==='succeeded'?'The private draft was saved. No data was shared.':'The draft could not be saved because its revision, profile or recipient changed. Reload it before editing.'}</p>
      <p>Receipt: <code>{outcome.receipt.receipt_id}</code></p><button className="bank-chat-logout" data-testid="review-agenda-actions" onClick={onReviewActions}>View recorded action</button></div>}
  </section>;
}
