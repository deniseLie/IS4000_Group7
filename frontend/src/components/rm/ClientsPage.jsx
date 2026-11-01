import {useEffect,useRef,useState} from 'react';
import Icon from '../Icon';
import {initials} from '../SharingPage';
import {FORMAT_WORDS,STATUS_WORDS,display,rmGet} from '../../lib/rm';
import {formatDate,formatMoney} from '../../lib/format';

const GAP_WORDS={no_open_compliance_review:'No open compliance review recorded',no_upcoming_appointment:'No upcoming appointment',
  no_recorded_preferences:'No client preferences recorded',no_meeting_notes:'No meeting notes recorded',no_interaction_in_180_days:'No recorded interaction in the last 180 days'};

// RM1/RM2: the RM's book (only assigned clients; a search outside it is refused and audited) and a record-based client
// overview where every block names its source. The assistant can explain or extend it.
export default function ClientsPage({token,onAsk,activeClient}) {
  const [book,setBook]=useState(null),[query,setQuery]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const [selected,setSelected]=useState(activeClient||null);
  const active=useRef(null);
  async function load(q='') {
    active.current?.abort();const controller=new AbortController();active.current=controller;setError('');setNotice('');
    try {
      const result=await rmGet(`/book${q?`?q=${encodeURIComponent(q)}`:''}`,await token(),controller.signal);
      if(!controller.signal.aborted){setBook(result.data);if(q&&!result.data.length)setNotice('No client in your book matches that name. Clients outside your book cannot be searched; the attempt is recorded.');}
    } catch(failure) {if(failure.name!=='AbortError')setError(failure.message);}
  }
  useEffect(()=>{load();return ()=>active.current?.abort();},[]);  // eslint-disable-line react-hooks/exhaustive-deps
  if(selected)return <ClientOverview customerId={selected} token={token} onAsk={onAsk} onBack={()=>setSelected(null)}/>;
  return <div className="app-page-body" data-testid="rm-clients">
    <header className="page-header"><div><span className="eyebrow">Relationship manager</span><h1>My clients</h1>
      <p>Only clients assigned to you are listed. Open a client for an overview built from their records.</p></div></header>
    <form className="rm-search" onSubmit={e=>{e.preventDefault();load(query.trim());}}><Icon name="search"/>
      <input aria-label="Search your clients" data-testid="rm-client-search" placeholder="Search your book by name" value={query} maxLength={80} onChange={e=>setQuery(e.target.value)}/>
      <button className="bank-chat-logout">Search</button>{query&&<button type="button" className="link-button" onClick={()=>{setQuery('');load();}}>Clear</button>}</form>
    {error&&<p role="alert">{error}</p>}{notice&&<p className="chat-notice-bar" role="status"><Icon name="lock"/><span>{notice}</span></p>}
    {!book&&!error&&<p className="loading" role="status">Loading your book…</p>}
    <ul className="rm-book">{(book||[]).map(c=><li key={c.customer_id}><button type="button" data-testid="rm-client" onClick={()=>setSelected(c.customer_id)}>
      <span className="session-avatar initials" aria-hidden="true">{initials(c.name)}</span>
      <span className="rm-book-main"><strong>{c.name}</strong><small>{c.income_range} · with you since {formatDate(c.assigned_since)}</small>
        <span className="rm-book-tags">{c.inherited_from&&<span className="category-badge">Inherited from {display(c.inherited_from)}</span>}
          {c.urgent&&<span className="ticket-status overdue">Urgent ticket</span>}{c.overdue>0&&<span className="ticket-status overdue">{c.overdue} past target</span>}
          {c.open_rm_tickets>0&&<span className="ticket-status submitted">{c.open_rm_tickets} for you</span>}
          {c.open_specialist_tickets>0&&<span className="ticket-status">{c.open_specialist_tickets} with specialists</span>}</span></span>
      <span className="rm-book-side"><small>Next meeting</small><strong>{c.next_appointment||'None booked'}</strong>{c.review_due&&<small>Review due {formatDate(c.review_due)}</small>}</span>
    </button></li>)}</ul>
  </div>;
}

function Block({title,source,asOf,children,empty}) {
  return <section className="relationship-details rm-block"><div className="rm-block-head"><h3>{title}</h3><small>Source: {source}{asOf?` · as of ${new Date(asOf).toLocaleTimeString('en-SG',{hour:'numeric',minute:'2-digit'})}`:''}</small></div>
    {empty?<p className="rm-none">{empty}</p>:children}</section>;
}

export function ClientOverview({customerId,token,onAsk,onBack}) {
  const [data,setData]=useState(null),[asOf,setAsOf]=useState(null),[error,setError]=useState('');
  useEffect(()=>{
    const controller=new AbortController();
    (async()=>{try{const r=await rmGet(`/clients/${customerId}`,await token(),controller.signal);setData(r.data);setAsOf(r.as_of);}
      catch(failure){if(failure.name!=='AbortError')setError(failure.message);}})();
    return ()=>controller.abort();
  },[customerId]);  // eslint-disable-line react-hooks/exhaustive-deps
  const ask=(message,intent)=>onAsk(message,{intent,context:{client_id:customerId}});
  if(error)return <div className="app-page-body"><button className="link-button" onClick={onBack}>← My clients</button><p role="alert">{error}</p></div>;
  if(!data)return <div className="app-page-body"><p className="loading" role="status">Loading the client’s records…</p></div>;
  const p=data.profile,rel=data.relationship||{},mine=data.tickets.filter(t=>t.rm_owned),theirs=data.tickets.filter(t=>!t.rm_owned);
  return <div className="app-page-body" data-testid="rm-client-overview">
    <button className="link-button breadcrumb-link" onClick={onBack}>← My clients</button>
    <header className="page-header"><div><span className="eyebrow">Client overview · records only</span><h1>{p.name}</h1>
      <p>{p.employment_status.replace(/_/g,' ')} · {p.income_range} · with you since {formatDate(rel.assigned_since)}{rel.inherited?` · inherited from ${rel.previous_advisor} on ${formatDate(rel.previous_to)}`:''}</p></div>
      <div className="page-header-actions">
        <button className="bank-chat-logout" onClick={()=>ask(`Give me an overview of ${p.name}`,'client_overview')}><Icon name="spark"/>Ask for an overview</button>
        <button className="bank-chat-logout" onClick={()=>ask(`Summarise past meetings with ${p.name}`,'meeting_history')}>Meeting history</button>
        {rel.inherited&&<button className="primary-button" data-testid="rm-handover" onClick={()=>ask(`Prepare a handover brief for ${p.name}`,'handover_brief')}>Handover brief</button>}
      </div></header>
    {data.gaps.length>0&&<p className="chat-notice-bar" role="status"><Icon name="info"/><span>Missing or unavailable: {data.gaps.map(g=>GAP_WORDS[g]||g).join(' · ')}.</span></p>}
    <div className="rm-overview-grid">
      <Block title="Accounts and cards" source="banking records" asOf={asOf} empty={!data.accounts.length&&!data.cards.length&&'None recorded'}>
        <ul className="rm-rows">{data.accounts.map(a=><li key={a.number}><span>{a.type.replace(/_/g,' ')} {a.number}</span><strong>{formatMoney(Number(a.balance),a.currency)}</strong></li>)}
          {data.cards.map(c=><li key={c.last4}><span>{c.name} ending {c.last4} · {c.status}</span><strong>{formatMoney(Number(c.outstanding_balance),c.currency)} of {formatMoney(Number(c.credit_limit),c.currency)}</strong></li>)}
          {data.loans.map((l,i)=><li key={i}><span>{l.type} loan</span><strong>{formatMoney(Number(l.outstanding_amount),l.currency)}</strong></li>)}</ul></Block>
      <Block title="Upcoming" source="appointments, compliance, maturities and your tasks" asOf={asOf}
        empty={!data.appointments.length&&!data.compliance&&!data.milestones.length&&!data.tasks.length&&'Nothing upcoming recorded'}>
        <ul className="rm-rows">{data.appointments.map(a=><li key={a.appointment_id}><span>{a.status==='held'?'Held (not confirmed)':'Meeting'} · {FORMAT_WORDS[a.format]} · {a.purpose||'purpose not recorded'}</span><strong>{a.when}</strong></li>)}
          {data.compliance&&<li><span>{data.compliance.title} · {data.compliance.outstanding_items.length} item(s) still to send</span><strong>Due {data.compliance.due_text}</strong></li>}
          {data.milestones.map((m,i)=><li key={i}><span>{m.title}{m.amount?` · ${formatMoney(Number(m.amount),m.currency)}`:''}</span><strong>{m.due_text}</strong></li>)}
          {data.tasks.map(t=><li key={t.task_id}><span>Your task: {t.title}</span><strong className={t.overdue?'rm-overdue':''}>{t.due_text||'No due date'}</strong></li>)}</ul></Block>
      <Block title="Requests for you" source="tickets" asOf={asOf} empty={!mine.length&&!data.rm_requests.length&&'No open requests for you'}>
        <ul className="rm-rows">{mine.map(t=><li key={t.reference}><span>{t.summary}</span><strong>{t.reference} · {STATUS_WORDS[t.status]}</strong></li>)}
          {data.rm_requests.map((r,i)=><li key={i}><span>Raised with {r.raised_with}: {r.what}</span><strong>{r.status}</strong></li>)}</ul></Block>
      <Block title="With specialist teams (read-only)" source="tickets, client-safe status" asOf={asOf} empty={!theirs.length&&'None open'}>
        <ul className="rm-rows">{theirs.map(t=><li key={t.reference}><span>{t.category_label} · {t.owner_team}{t.restricted?' · detail restricted':''}</span><strong>{t.reference} · {t.client_safe_status}</strong></li>)}</ul></Block>
      <Block title="What the client has told us" source="client-stated preferences" asOf={asOf} empty={!data.preferences.length&&'No preferences recorded'}>
        <ul className="rm-rows">{data.preferences.map((f,i)=><li key={i}><span><span className="rm-kind client_stated">Client said</span> {f.preference}</span><strong>{f.recorded_text}</strong></li>)}</ul>
        {data.shared_note_available&&<p className="rm-small"><Icon name="lock"/>A handover note is shared with you under active consent. Ask for a briefing to use it.</p>}</Block>
      <Block title="Recent interactions" source="meeting notes you may see" asOf={asOf} empty={!data.interactions.length&&'No meeting notes recorded'}>
        <ul className="rm-rows">{data.interactions.map(n=><li key={n.note_id}><span>{n.title||FORMAT_WORDS[n.format]} · by {n.written_by_current_rm?'you':`${n.author} (previous RM)`}</span><strong>{n.met_on_text}</strong></li>)}</ul></Block>
    </div>
    <div className="rm-overview-actions">
      <button className="bank-chat-logout" data-testid="rm-document-call" onClick={()=>onAsk(`Notes from my call today with ${p.name}: `,{context:{client_id:customerId},prefill:true})}><Icon name="document"/>Document a call or meeting</button>
      <button className="bank-chat-logout" onClick={()=>ask(`What should I prepare for my next meeting with ${p.name}?`,'meeting_brief')}><Icon name="calendar"/>Brief me for the next meeting</button>
      <button className="bank-chat-logout" onClick={()=>ask(`Draft a message to ${p.name}`,'draft_message')}><Icon name="chat"/>Draft a message</button>
    </div>
  </div>;
}
