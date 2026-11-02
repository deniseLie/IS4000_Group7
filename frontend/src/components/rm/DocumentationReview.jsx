import {useState} from 'react';
import Icon from '../Icon';
import ServiceRequestForm from './ServiceRequestForm';
import {FORMAT_WORDS,interactionPayload,newId,rmPost,taskFromAction} from '../../lib/rm';

const CATEGORY_WORDS={fact:'Fact',decision:'Decision',commitment:'Commitment',client_request:'Client request',topic:'Topic'};
const ORIGIN_WORDS={notes:'From your notes',rm_correction:'Your correction',ai_inference:'AI inference – check'};
const ACTION_WORDS={client_note:'Client note',follow_up_task:'Follow-up task',service_request:'Service request',meeting:'Meeting'};

// RM5 → RM10 / RM6: review the structured documentation, approve it into the client record, then confirm each proposed
// task or service request separately. Nothing is saved, created or submitted without an explicit approval here.
export default function DocumentationReview({documentation,token,onChange,latest,onRecap}) {
  const draft=documentation.draft||{items:[],actions:[],gaps:[]};
  const [edits,setEdits]=useState({met_on:draft.interaction?.met_on||new Date().toISOString().slice(0,10),format:['video','phone','in_person'].includes(draft.interaction?.format)?draft.interaction.format:'',
    title:draft.interaction?.title||'',summary:draft.summary||'',items:draft.items.map(i=>({...i}))});
  const [noteId,setNoteId]=useState(documentation.note_id||null);
  const [saving,setSaving]=useState({busy:false,error:''});
  const [decisions,setDecisions]=useState({});  // action id -> 'rejected' | {task} | {reference}
  const [tasks,setTasks]=useState(()=>Object.fromEntries(draft.actions.filter(a=>a.kind==='follow_up_task').map(a=>[a.id,{...taskFromAction(a),selected:!a.duplicate_of}])));
  const [taskState,setTaskState]=useState({busy:false,error:'',result:null});
  const [openRequest,setOpenRequest]=useState(null);
  const saved=!!noteId;
  const existingNote=!!documentation.from_note;

  async function approveDocumentation() {
    if(!edits.format){setSaving({busy:false,error:'Choose how the interaction took place.'});return;}
    if(!edits.summary.trim()){setSaving({busy:false,error:'The summary cannot be empty.'});return;}
    setSaving({busy:true,error:''});
    try {
      const result=await rmPost('/interactions',interactionPayload(documentation,edits,newId()),await token());
      setNoteId(result.note_id);setSaving({busy:false,error:''});
      onChange?.({...documentation,note_id:result.note_id});
    } catch(failure) {setSaving({busy:false,error:failure.message});}
  }
  async function createTasks() {
    const chosen=draft.actions.filter(a=>a.kind==='follow_up_task'&&tasks[a.id]?.selected&&decisions[a.id]!=='rejected');
    if(!chosen.length)return;
    setTaskState({busy:true,error:'',result:null});
    try {
      const body={customer_id:documentation.customer_id,note_id:noteId,tasks:chosen.map(a=>{const {selected,...task}=tasks[a.id];return {...task,due_on:task.due_on||null};})};
      const result=await rmPost('/tasks',body,await token());
      setTaskState({busy:false,error:'',result});
      setDecisions(d=>({...d,...Object.fromEntries(chosen.map(a=>[a.id,'created']))}));
    } catch(failure) {setTaskState({busy:false,error:failure.message,result:null});}
  }
  const item=(key,patch)=>setEdits(e=>({...e,items:e.items.map((x,i)=>i===key?{...x,...patch}:x)}));
  const ready=saved||existingNote;
  // Once every proposed task and request has been created, submitted, skipped or rejected, say plainly what was recorded.
  const actionable=draft.actions.filter(a=>a.kind==='follow_up_task'||a.kind==='service_request');
  const skipped=a=>a.kind==='follow_up_task'&&!!taskState.result&&!tasks[a.id]?.selected&&!decisions[a.id];
  const finished=ready&&actionable.every(a=>decisions[a.id]||skipped(a));
  const created=actionable.filter(a=>decisions[a.id]==='created'),requests=actionable.filter(a=>typeof decisions[a.id]==='object'&&decisions[a.id]);
  const notDone=actionable.filter(a=>decisions[a.id]==='rejected'||skipped(a));
  const remaining=draft.actions.some(a=>a.kind==='follow_up_task'&&!decisions[a.id]&&!skipped(a));
  const locked=!latest&&!saved;
  return <section className="rmw-review documentation" data-testid="rm-documentation">
    <div className="rmw-review-head"><strong><Icon name="document"/>{existingNote?'Follow-ups from a saved note':saved?'Interaction saved to the client record':'Interaction documentation – not saved'}</strong>
      <span className="category-badge">{documentation.client} · revision {documentation.revision}</span></div>
    {!existingNote&&<>
      <div className="rm-form-grid">
        <label className="form-field">Date<input type="date" value={edits.met_on} disabled={saved||locked} onChange={e=>setEdits({...edits,met_on:e.target.value})}/></label>
        <label className="form-field">How<select value={edits.format} disabled={saved||locked} onChange={e=>setEdits({...edits,format:e.target.value})}>
          <option value="">Choose…{draft.interaction?.format==='unknown'?' (not stated in notes)':''}</option>{['video','phone','in_person'].map(f=><option key={f} value={f}>{FORMAT_WORDS[f]}</option>)}</select></label>
        <label className="form-field rm-span">Title<input value={edits.title} maxLength={160} disabled={saved||locked} onChange={e=>setEdits({...edits,title:e.target.value})}/></label>
      </div>
      <label className="form-field">Summary<textarea rows={4} maxLength={2000} value={edits.summary} disabled={saved||locked} onChange={e=>setEdits({...edits,summary:e.target.value})}/></label>
      <h4>Extracted from your notes</h4>
      <ul className="rm-extract">{edits.items.map((x,i)=><li key={i} className={x.removed?'removed':''}>
        <span className="category-badge">{CATEGORY_WORDS[x.category]}</span>
        <span className={`rm-certainty ${x.certainty}`}>{x.certainty==='tentative'?'Tentative':'Confirmed'}</span>
        <input value={x.text} disabled={saved||locked||x.removed} aria-label="Extracted text" onChange={e=>item(i,{text:e.target.value,origin:'rm_correction'})}/>
        <small className={`rm-origin ${x.origin}`}>{ORIGIN_WORDS[x.origin]}{x.owner?` · owner: ${x.owner}`:''}{x.due?` · due: ${x.due}`:''}</small>
        {!saved&&<button type="button" className="link-button" disabled={locked} onClick={()=>item(i,{removed:!x.removed})}>{x.removed?'Keep':'Remove'}</button>}
      </li>)}</ul>
      {draft.gaps?.length>0&&<><h4>To clarify</h4><ul className="rm-cautions">{draft.gaps.map(g=><li key={g}><Icon name="alert"/>{g}</li>)}</ul></>}
      <details className="rm-small"><summary>Your original notes{documentation.corrections?.length?` and ${documentation.corrections.length} correction(s)`:''}</summary>
        <p className="rm-quote">{documentation.source_notes}</p>{documentation.corrections?.map((c,i)=><p key={i} className="rm-quote correction">Correction {i+1}: {c}</p>)}</details>
      {!saved&&<p className="rm-small">Correct anything here, or type a correction in the chat (for example “the statement is for August, not September”). Your corrections are kept with the record.</p>}
      {saving.error&&<p role="alert" className="rm-error">{saving.error}</p>}
      {documentation.appointment_id&&<p className="rm-small"><Icon name="calendar"/> Documents the selected meeting: it will be linked to that appointment when you save.</p>}
      {saved?<div className="rm-saved-row"><p role="status" className="rm-success" data-testid="rm-documentation-saved"><Icon name="check"/>Saved to the client record with your notes and corrections{documentation.appointment_id?', linked to the meeting':''}. Proposed actions below can now be confirmed.</p>
        {onRecap&&<button type="button" className="bank-chat-logout" data-testid="rm-draft-recap" onClick={()=>onRecap(documentation.client)}><Icon name="chat"/>Draft a recap for {documentation.client?.split(' ')[0]||'the client'}</button>}</div>
        :<div className="panel-actions"><button type="button" className="primary-button" data-testid="rm-documentation-approve" disabled={saving.busy||locked} onClick={approveDocumentation}>{saving.busy?'Saving…':'Approve and save to client record'}<Icon name="check"/></button></div>}
    </>}
    <h4>Proposed actions <small className="rm-small">{ready?'confirm each one separately':'available once the documentation is saved'}</small></h4>
    {!draft.actions.length&&<p className="rm-small">No actions were found in the notes.</p>}
    <ul className="rm-actions">{draft.actions.map(a=><li key={a.id} className={`rm-action ${decisions[a.id]==='rejected'?'removed':''}`} data-kind={a.kind}>
      <div className="rm-action-head"><span className="category-badge">{ACTION_WORDS[a.kind]}</span><strong>{a.title}</strong>
        {a.origin!=='notes'&&<small className={`rm-origin ${a.origin}`}>{ORIGIN_WORDS[a.origin]}</small>}</div>
      <p className="rm-quote">“{a.evidence_quote}”</p>
      {a.flags?.length>0&&<ul className="rm-cautions">{a.flags.map(f=><li key={f}><Icon name="alert"/>{f}</li>)}</ul>}
      {a.kind==='follow_up_task'&&tasks[a.id]&&decisions[a.id]!=='rejected'&&<div className="rm-form-grid">
        <label className="form-field rm-span">Task<input value={tasks[a.id].title} maxLength={200} disabled={!ready||decisions[a.id]==='created'} onChange={e=>setTasks({...tasks,[a.id]:{...tasks[a.id],title:e.target.value}})}/></label>
        <label className="form-field">Owner<input value={tasks[a.id].owner} maxLength={80} placeholder="Not stated" disabled={!ready||decisions[a.id]==='created'} onChange={e=>setTasks({...tasks,[a.id]:{...tasks[a.id],owner:e.target.value}})}/></label>
        <label className="form-field">Due<input type="date" value={tasks[a.id].due_on||''} disabled={!ready||decisions[a.id]==='created'} onChange={e=>setTasks({...tasks,[a.id]:{...tasks[a.id],due_on:e.target.value||null}})}/></label>
        <label className="review-summary"><input type="checkbox" checked={tasks[a.id].selected} disabled={!ready||decisions[a.id]==='created'} onChange={e=>setTasks({...tasks,[a.id]:{...tasks[a.id],selected:e.target.checked}})}/> Include when I create tasks</label>
        {decisions[a.id]==='created'&&<p className="rm-success"><Icon name="check"/>Task created and linked to this interaction.</p>}</div>}
      {a.kind==='service_request'&&decisions[a.id]!=='rejected'&&(typeof decisions[a.id]==='object'
        ?<p className="rm-success" data-testid="rm-request-created"><Icon name="check"/>Request {decisions[a.id].reference} created with the {decisions[a.id].owner_team}.</p>
        :openRequest===a.id?<ServiceRequestForm action={a} customerId={documentation.customer_id} noteId={noteId} token={token}
          onDone={result=>{setDecisions({...decisions,[a.id]:result});setOpenRequest(null);}} onCancel={()=>setOpenRequest(null)}/>
        :<button type="button" className="bank-chat-logout" disabled={!ready} onClick={()=>setOpenRequest(a.id)}>Review request form</button>)}
      {a.kind==='meeting'&&<p className="rm-small">Meetings are booked by the client from your free times (My requests → Meetings). Check your availability on Schedule.</p>}
      {a.kind==='client_note'&&<p className="rm-small">Included in the saved interaction summary.</p>}
      {decisions[a.id]!=='created'&&typeof decisions[a.id]!=='object'&&<button type="button" className="link-button" onClick={()=>setDecisions({...decisions,[a.id]:decisions[a.id]==='rejected'?undefined:'rejected'})}>{decisions[a.id]==='rejected'?'Undo reject':'Reject'}</button>}
    </li>)}</ul>
    {finished&&<div className="rm-complete" role="status" data-testid="rm-documentation-complete">
      <strong><Icon name="check"/>All done for this {existingNote?'note':'call'}. Nothing else needs your approval.</strong>
      <ul>
        {!existingNote&&<li>Interaction saved to {documentation.client}’s record ({edits.met_on}{edits.title?` · ${edits.title}`:''}). Find it under My clients → Meetings & notes.</li>}
        {created.length>0&&<li>{created.length} task{created.length===1?'':'s'} created: {created.map(a=>tasks[a.id]?.title||a.title).join('; ')}. They appear in Follow-ups.</li>}
        {requests.map(a=><li key={a.id}>Request {decisions[a.id].reference} sent to the {decisions[a.id].owner_team}. {documentation.client} sees it in My requests.</li>)}
        {notDone.length>0&&<li>Not created: {notDone.map(a=>a.title).join('; ')}.</li>}
        {!actionable.length&&<li>No tasks or requests were proposed from these notes.</li>}
      </ul></div>}
    {taskState.error&&<p role="alert" className="rm-error">{taskState.error}</p>}
    {taskState.result?.duplicates?.length>0&&<p className="rm-cautions">Not created because a matching open task exists: {taskState.result.duplicates.map(d=>d.title).join('; ')}.</p>}
    {remaining&&<div className="panel-actions">
      <button type="button" className="primary-button" data-testid="rm-create-tasks" disabled={!ready||taskState.busy||!draft.actions.some(a=>a.kind==='follow_up_task'&&tasks[a.id]?.selected&&!decisions[a.id])} onClick={createTasks}>{taskState.busy?'Creating…':'Create selected tasks'}</button></div>}
  </section>;
}
