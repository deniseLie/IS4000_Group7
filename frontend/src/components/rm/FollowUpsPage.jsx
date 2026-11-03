import {useEffect,useState} from 'react';
import Icon from '../Icon';
import {followupGroups,rmGet,rmPatch,rmPost} from '../../lib/rm';

const GROUPS=[['overdue','Overdue'],['week','Next 7 days'],['later','Later'],['commitments','Recorded commitments (completion not tracked)'],['specialist','Specialist-managed, for awareness']];
function sourceLabel(id) {
  if(id.startsWith('ticket:'))return id.slice(7);
  if(id.startsWith('note:'))return 'meeting note';
  if(id.startsWith('task:'))return 'your task list';
  return id.replace(/[-_]/g,' ');
}
const KIND_WORDS={rm_ticket:'Your request',specialist_ticket:'Specialist case',review_deadline:'Review deadline',milestone:'Maturity / review',task:'Your task',meeting:'Confirmed meeting',commitment:'Commitment'};

// RM11 (and RM10 tasks): follow-ups computed live from the records, each with its reason, date and source. The RM can
// dismiss an item, open it with the assistant, or ask the assistant to prioritise. Nothing is sent to a client from here.
export default function FollowUpsPage({token,onAsk,today}) {
  const [items,setItems]=useState(null),[tasks,setTasks]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  async function load() {
    setError('');
    try {const auth=await token();const [f,t]=await Promise.all([rmGet('/followups',auth),rmGet('/tasks',auth)]);setItems(f.data);setTasks(t.data);}
    catch(failure) {setError(failure.message);}
  }
  useEffect(()=>{load();},[]);  // eslint-disable-line react-hooks/exhaustive-deps
  async function act(operation) {setBusy(true);try{await operation(await token());await load();}catch(f){setError(f.message);}finally{setBusy(false);}}
  const groups=followupGroups(items,today||new Date().toISOString().slice(0,10));
  return <div className="app-page-body" data-testid="rm-followups">
    <header className="page-header"><div><span className="eyebrow">Relationship manager</span><h1>Follow-ups</h1>
      <p>Built from your clients’ records each time you open it: tickets, deadlines, maturities, tasks, meetings and recorded commitments.</p></div>
      <div className="page-header-actions"><button className="bank-chat-logout" onClick={load} disabled={busy}><Icon name="refresh"/>Refresh</button>
        <button className="primary-button" data-testid="rm-prioritise" onClick={()=>onAsk('Which clients should I follow up with this week, and why?',{intent:'priorities'})}><Icon name="spark"/>Prioritise with assistant</button></div></header>
    {error&&<p role="alert">{error}</p>}
    {!items&&!error&&<p className="loading" role="status">Reading your book…</p>}
    {items&&GROUPS.map(([key,label])=>groups[key].length>0&&<section key={key} className="rm-follow-group"><h2 className="meetings-title">{label} <span className="count">{groups[key].length}</span></h2>
      <ul className="ticket-list">{groups[key].map(item=><li key={item.item_key} data-testid="rm-followup" className={item.overdue?'rm-overdue-item':''}>
        <div className="ticket-head"><strong>{item.client} · {item.title}</strong><span className="category-badge">{KIND_WORDS[item.kind]||item.kind}</span></div>
        {item.detail&&<p>{item.detail}</p>}
        <small>{item.due_text?`${item.overdue?'Was due':'Due'} ${item.due_text} · `:''}source: {sourceLabel(item.source_id)}{item.confirmed_meeting?` · meeting confirmed ${item.confirmed_meeting}`:''}</small>
        <div className="rm-ticket-actions">
          <button className="bank-chat-logout" onClick={()=>onAsk(`Tell me about this follow-up for ${item.client}: ${item.title}`,{intent:item.kind==='meeting'?'meeting_brief':item.kind.includes('ticket')?'ticket_summary':'client_overview',
            context:{client_id:item.customer_id,...(item.source_id.startsWith('ticket:')&&{ticket_ref:item.source_id.slice(7)})}})}><Icon name="spark"/>Ask assistant</button>
          <button className="link-button" disabled={busy} onClick={()=>act(auth=>rmPost('/followups/dismiss',{item_key:item.item_key},auth))}>Dismiss</button></div>
      </li>)}</ul></section>)}
    {items&&!items.length&&<p className="page-empty">Nothing to follow up right now.</p>}
    <h2 className="meetings-title">Your tasks</h2>
    {tasks&&!tasks.length&&<p className="page-empty">No tasks.</p>}
    <ul className="ticket-list">{(tasks||[]).map(t=><li key={t.task_id} data-testid="rm-task" className={t.status!=='open'?'removed':''}>
      <div className="ticket-head"><strong>{t.client} · {t.title}</strong><span className={`ticket-status ${t.status==='open'?(t.overdue?'overdue':'submitted'):'resolved'}`}>{t.status==='open'?(t.overdue?'Overdue':'Open'):t.status}</span></div>
      <small>Owner {t.owner||'not set'} · {t.due_text?`due ${t.due_text}`:'no due date'}{t.note_date?` · from the ${t.note_date} interaction`:''}</small>
      {t.status==='open'&&<div className="rm-ticket-actions"><button className="bank-chat-logout" disabled={busy} onClick={()=>act(auth=>rmPatch(`/tasks/${t.task_id}`,{status:'done'},auth))}><Icon name="check"/>Mark done</button></div>}
    </li>)}</ul>
  </div>;
}
