import {useEffect,useRef,useState} from 'react';
import Icon from '../Icon';
import {STATUS_WORDS,receivedText,rmGet,ticketsByOwner} from '../../lib/rm';

const PRIORITIES=[['all','All priorities'],['urgent','Urgent'],['high','High'],['normal','Normal'],['low','Low']];
const URGENCY_WORDS={urgent:'Urgent',high:'High',normal:'Normal',low:'Low'};

// RM7/RM8: "My tickets" are requests Sophie owns (summarise, draft a reply). "Specialist tickets" are owned and decided by
// another team (fraud, payments, cards, login): she only sees their client-safe status, so there is nothing to act on
// here; the client and the specialist team deal with each other directly. Restricted detail is never shown.
export default function RmTicketsPage({token,onAsk}) {
  const [data,setData]=useState(null),[error,setError]=useState(''),[tab,setTab]=useState('mine'),[open,setOpen]=useState(null),[priority,setPriority]=useState('all');
  const active=useRef(null);
  async function load() {
    active.current?.abort();const controller=new AbortController();active.current=controller;setError('');
    try {const r=await rmGet('/tickets',await token(),controller.signal);if(!controller.signal.aborted)setData(r.data);}
    catch(failure) {if(failure.name!=='AbortError')setError(failure.message);}
  }
  useEffect(()=>{load();return ()=>active.current?.abort();},[]);  // eslint-disable-line react-hooks/exhaustive-deps
  const groups=ticketsByOwner(data);
  const shown=groups[tab].filter(t=>priority==='all'||t.urgency===priority);
  return <div className="app-page-body rmw-page" data-testid="rm-tickets">
    <header className="page-header"><div><span className="eyebrow">Relationship manager</span><h1>Ticket inbox</h1>
      <p>Your requests come first. Specialist teams own and decide their own cases; you can see what the client sees.</p></div>
      <div className="page-header-actions"><button className="bank-chat-logout" onClick={load}><Icon name="refresh"/>Refresh</button>
        <button className="primary-button" onClick={()=>onAsk('What needs my attention today?',{intent:'ticket_inbox'})}><Icon name="spark"/>Triage with assistant</button></div></header>
    <div className="rmw-tabs-row">
      <nav className="page-tabs" role="tablist">
        <button role="tab" aria-selected={tab==='mine'} data-testid="rm-tab-mine" onClick={()=>{setTab('mine');setOpen(null);}}>My tickets<span className="count">{groups.mine.length}</span></button>
        <button role="tab" aria-selected={tab==='specialist'} data-testid="rm-tab-specialist" onClick={()=>{setTab('specialist');setOpen(null);}}>Specialist tickets<span className="count">{groups.specialist.length}</span></button></nav>
      <label className="pill-select rmw-filter" title="Filter by priority"><Icon name="alert"/><select aria-label="Priority" value={priority} onChange={e=>setPriority(e.target.value)}>{PRIORITIES.map(([v,l])=><option key={v} value={v}>{l}</option>)}</select><Icon name="chevron"/></label>
    </div>
    {tab==='specialist'&&<p className="rmw-note"><Icon name="info"/>Read-only. These teams decide the case and update the client themselves. If the client asks you, share the status shown here; anything else goes to the team.</p>}
    {error&&<p role="alert">{error}</p>}
    {!data&&!error&&<p className="loading" role="status">Loading tickets…</p>}
    {data&&!shown.length&&<p className="page-empty">{priority==='all'?'Nothing here.':'No tickets with this priority.'}</p>}
    <ul className="rmw-tickets tab-fade" key={tab}>{shown.map(t=><li key={t.reference} data-testid="rm-ticket" data-reference={t.reference} className={`rmw-ticket urgency-${t.urgency}${t.overdue?' overdue':''}`}>
      <div className="rmw-ticket-head">
        <span className={`rmw-urgency ${t.urgency}`}>{URGENCY_WORDS[t.urgency]||t.urgency}</span>
        <strong>{t.client} · {t.category_label}</strong>
        <span className={`ticket-status ${t.status}`}>{STATUS_WORDS[t.status]}</span></div>
      <p className="rmw-ticket-meta"><code>{t.reference}</code><span>{receivedText(t.age_hours)}</span>
        <span className={t.overdue?'rmw-late':''}>{t.overdue?'Overdue · was due':'Due'} {t.target_sgt}</span>
        {!t.rm_owned&&<span>Owner: {t.owner_team}</span>}</p>
      {t.rm_owned?<p className="rmw-ticket-text">{t.summary}</p>
        :t.restricted?<p className="rmw-ticket-text rm-none"><Icon name="lock"/>Details are restricted to {t.owner_team}.</p>:<p className="rmw-ticket-text">{t.summary}</p>}
      <p className="rmw-ticket-status"><Icon name="info"/>{t.rm_owned?(t.last_update?.note?`Latest update: ${t.last_update.note}`:'No update sent to the client yet.'):`Client sees: ${t.client_safe_status}`}</p>
      <div className="rm-ticket-actions">
        {t.rm_owned&&<><button className="primary-button small" onClick={()=>onAsk(`Draft a reply to ${t.client} about ${t.reference}`,{intent:'draft_message',context:{client_id:t.customer_id,ticket_ref:t.reference}})}><Icon name="chat"/>Draft a reply</button>
          <button className="bank-chat-logout" onClick={()=>onAsk(`Summarise ticket ${t.reference}`,{intent:'ticket_summary',context:{client_id:t.customer_id,ticket_ref:t.reference}})}><Icon name="spark"/>Summarise</button></>}
        <button className="bank-chat-logout" aria-expanded={open===t.reference} onClick={()=>setOpen(open===t.reference?null:t.reference)}>{open===t.reference?'Hide details':t.rm_owned?'Details':'View status history'}<Icon name="chevron"/></button></div>
      {open===t.reference&&<TicketDetail reference={t.reference} token={token}/>}
    </li>)}</ul>
  </div>;
}

function TicketDetail({reference,token}) {
  const [t,setT]=useState(null),[error,setError]=useState('');
  useEffect(()=>{const c=new AbortController();(async()=>{try{setT((await rmGet(`/tickets/${reference}`,await token(),c.signal)).data);}catch(f){if(f.name!=='AbortError')setError(f.message);}})();return ()=>c.abort();},[reference]);  // eslint-disable-line react-hooks/exhaustive-deps
  if(error)return <p role="alert">{error}</p>;
  if(!t)return <p className="loading" role="status">Loading…</p>;
  const told=[...t.told_by_bank.map(e=>({at:e.at_sgt,who:'Bank',text:e.note})),...t.told_by_assistant.map(e=>({at:e.at_sgt,who:'Chat assistant',text:e.reply})),
    ...t.sent_by_rm.map(e=>({at:e.at_sgt,who:'You',text:e.text}))];
  const words=v=>String(v).replace(/_/g,' ').replace(/^./,c=>c.toUpperCase());
  const fields=Object.entries(t.details||{});
  return <div className="rmw-ticket-detail" data-testid="rm-ticket-detail">
    <div className="rmw-detail-col">
      <h4>Request details</h4>
      <dl className="rmw-kv">
        <div><dt>Reference</dt><dd><code>{t.reference}</code></dd></div>
        <div><dt>Owner</dt><dd>{t.rm_owned?'You':t.owner_team}</dd></div>
        <div><dt>Received</dt><dd>{t.created_sgt||'Not recorded'}</dd></div>
        {fields.map(([k,v])=><div key={k}><dt>{words(k)}</dt><dd>{words(v)}</dd></div>)}
        {t.attachments?.length>0&&<div><dt>Attached</dt><dd>{t.attachments.join(', ')}</dd></div>}
      </dl>
      <p className="rmw-decides"><Icon name="shield"/><span><strong>Who decides</strong>{t.category_info?.decided_by||t.owner_team}</span></p>
      {t.rm_owned&&t.handover_context&&<><h4>Handover context (internal)</h4><p className="rm-quote">{t.handover_context}</p></>}
    </div>
    <div className="rmw-detail-col">
      <h4>What the client has been told</h4>
      {told.length?<ol className="rmw-told">{told.map((e,i)=><li key={i}><span className="rmw-told-who">{e.who}</span><small>{e.at}</small><p>{e.text}</p></li>)}</ol>:<p className="rm-none">Nothing recorded yet.</p>}
      {t.client_messages?.length>0&&<><h4>Client’s messages in the chat</h4><ol className="rmw-told">{t.client_messages.map((m,i)=><li key={i}><span className="rmw-told-who">Client</span><small>{m.at_sgt}</small><p>{m.text}</p></li>)}</ol></>}
    </div>
  </div>;
}
