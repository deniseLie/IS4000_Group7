import {useEffect,useRef,useState} from 'react';
import Icon from '../Icon';
import Panel from '../Panel';
import {STATUS_WORDS,ageText,rmGet,ticketsByOwner} from '../../lib/rm';

// RM7/RM8: the RM's own tickets (urgency, owner, age, target) and a separate read-only view of specialist-managed
// requests with client-safe status. Restricted specialist detail (fraud, login) is never shown.
export default function RmTicketsPage({token,onAsk}) {
  const [data,setData]=useState(null),[error,setError]=useState(''),[tab,setTab]=useState('mine'),[open,setOpen]=useState(null);
  const active=useRef(null);
  async function load() {
    active.current?.abort();const controller=new AbortController();active.current=controller;setError('');
    try {const r=await rmGet('/tickets',await token(),controller.signal);if(!controller.signal.aborted)setData(r.data);}
    catch(failure) {if(failure.name!=='AbortError')setError(failure.message);}
  }
  useEffect(()=>{load();return ()=>active.current?.abort();},[]);  // eslint-disable-line react-hooks/exhaustive-deps
  const groups=ticketsByOwner(data);
  const shown=groups[tab];
  return <div className="app-page-body" data-testid="rm-tickets">
    <header className="page-header"><div><span className="eyebrow">Relationship manager</span><h1>Ticket inbox</h1>
      <p>Requests you own come first. Specialist teams decide their own cases; you see their client-safe status.</p></div>
      <div className="page-header-actions"><button className="bank-chat-logout" onClick={load}><Icon name="refresh"/>Refresh</button>
        <button className="primary-button" onClick={()=>onAsk('What needs my attention today?',{intent:'ticket_inbox'})}><Icon name="spark"/>Triage with assistant</button></div></header>
    <nav className="page-tabs" role="tablist">
      <button role="tab" aria-selected={tab==='mine'} data-testid="rm-tab-mine" onClick={()=>setTab('mine')}>Needs your action<span className="count">{groups.mine.length}</span></button>
      <button role="tab" aria-selected={tab==='specialist'} data-testid="rm-tab-specialist" onClick={()=>setTab('specialist')}>With specialist teams (read-only)<span className="count">{groups.specialist.length}</span></button></nav>
    {error&&<p role="alert">{error}</p>}
    {!data&&!error&&<p className="loading" role="status">Loading tickets…</p>}
    {data&&!shown.length&&<p className="page-empty">Nothing here.</p>}
    <ul className="ticket-list tab-fade" key={tab}>{shown.map(t=><li key={t.reference} data-testid="rm-ticket" data-reference={t.reference} className={`rm-ticket urgency-${t.urgency}`}>
      <div className="ticket-head"><strong>{t.client} · {t.category_label}</strong>
        <span className={`ticket-status ${t.status}${t.overdue?' overdue':''}`}>{STATUS_WORDS[t.status]}{t.overdue?' · past target':''}</span></div>
      <p>{t.summary??<span className="rm-none"><Icon name="lock"/> Detail restricted to the {t.owner_team}</span>}</p>
      <p className="ticket-next"><Icon name="info"/>{t.rm_owned?(t.last_update?.note||'No update recorded yet.'):`Client sees: ${t.client_safe_status}`}</p>
      <small><span className={`rmw-urgency ${t.urgency}`}>{t.urgency}</span> · <code>{t.reference}</code> · owner {t.owner_team} · age {ageText(t.age_hours)} · target {t.target_sgt}</small>
      <div className="rm-ticket-actions"><button className="bank-chat-logout" onClick={()=>setOpen(t.reference)}>Details</button>
        <button className="bank-chat-logout" onClick={()=>onAsk(`Summarise ticket ${t.reference}`,{intent:'ticket_summary',context:{client_id:t.customer_id,ticket_ref:t.reference}})}><Icon name="spark"/>Summarise</button>
        {!(t.restricted&&!t.rm_owned)&&<button className="bank-chat-logout" onClick={()=>onAsk(`Draft a reply to ${t.client} about ${t.reference}`,{intent:'draft_message',context:{client_id:t.customer_id,ticket_ref:t.reference}})}>Draft a reply</button>}</div>
    </li>)}</ul>
    {open&&<TicketDetail reference={open} token={token} onClose={()=>setOpen(null)}/>}
  </div>;
}

function TicketDetail({reference,token,onClose}) {
  const [t,setT]=useState(null),[error,setError]=useState('');
  useEffect(()=>{const c=new AbortController();(async()=>{try{setT((await rmGet(`/tickets/${reference}`,await token(),c.signal)).data);}catch(f){if(f.name!=='AbortError')setError(f.message);}})();return ()=>c.abort();},[reference]);  // eslint-disable-line react-hooks/exhaustive-deps
  return <Panel label="Ticket" closeLabel="Close ticket" eyebrow={reference} title={t?`${t.client} · ${t.category_label}`:'Loading…'} size="drawer" onClose={onClose}>
    {error&&<p role="alert">{error}</p>}
    {t&&<div className="rm-ticket-detail" data-testid="rm-ticket-detail">
      <p><span className={`ticket-status ${t.status}`}>{STATUS_WORDS[t.status]}</span> · {t.urgency} · owner {t.owner_team} · target {t.target_sgt}</p>
      {t.summary?<p>{t.summary}</p>:<p className="rm-none"><Icon name="lock"/> Restricted to the {t.owner_team}. Only the client-safe status is available to you.</p>}
      {t.details&&Object.keys(t.details).length>0&&<dl className="rm-source-fields">{Object.entries(t.details).map(([k,v])=><div key={k}><dt>{k.replace(/_/g,' ')}</dt><dd>{v}</dd></div>)}</dl>}
      <h4>What the client was told</h4>
      <ul className="rm-timeline">{[...t.told_by_bank.map(e=>({at:e.at_sgt,who:'Bank',text:e.note})),...t.told_by_assistant.map(e=>({at:e.at_sgt,who:'Chat assistant',text:e.reply})),
        ...t.sent_by_rm.map(e=>({at:e.at_sgt,who:'Relationship manager',text:e.text}))].map((e,i)=><li key={i}><small>{e.at} · {e.who}</small><p>{e.text}</p></li>)}</ul>
      {t.rm_owned&&t.handover_context&&<><h4>Handover context (internal)</h4><p className="rm-quote">{t.handover_context}</p></>}
      {t.client_messages?.length>0&&<><h4>Client’s messages in the chat</h4><ul className="rm-timeline">{t.client_messages.map((m,i)=><li key={i}><small>{m.at_sgt}</small><p>{m.text}</p></li>)}</ul></>}
      {t.attachments?.length>0&&<p><Icon name="clip"/>Attached: {t.attachments.join(', ')}</p>}
      <h4>Who decides</h4><p>{t.category_info?.decided_by}</p>
      <p className="page-note"><Icon name="shield"/>Viewing or summarising a ticket never changes or closes it.</p>
    </div>}
  </Panel>;
}
