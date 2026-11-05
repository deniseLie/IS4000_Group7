import {useEffect,useRef,useState} from 'react';
import Icon from '../Icon';
import RmAnswer from './RmAnswer';
import {FORMAT_WORDS,STATUS_WORDS,askAssistant,display,filterBook,requestContext,rmGet} from '../../lib/rm';
import {formatDate,formatMoney,initials} from '../../lib/format';

const GAP_WORDS={no_open_compliance_review:'No open compliance review recorded',no_upcoming_appointment:'No upcoming appointment',
  no_recorded_preferences:'No client preferences recorded',no_meeting_notes:'No meeting notes recorded',no_interaction_in_180_days:'No recorded interaction in the last 180 days'};
const words=value=>(value||'').replace(/_/g,' ').replace(/^./,c=>c.toUpperCase());

// RM1/RM2: the RM's book (only assigned clients) with instant search, and a client page built from the records, where
// every card names its source. Searching for a name outside the book returns nothing and the attempt is recorded.
export default function ClientsPage({token,onAsk}) {
  const [book,setBook]=useState(null),[query,setQuery]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const [selected,setSelected]=useState(null);
  useEffect(()=>{
    const controller=new AbortController();
    (async()=>{try{setBook((await rmGet('/book',await token(),controller.signal)).data);}catch(failure){if(failure.name!=='AbortError')setError(failure.message);}})();
    return ()=>controller.abort();
  },[]);  // eslint-disable-line react-hooks/exhaustive-deps
  const shown=filterBook(book,query);
  // Enter with no match asks the server, which refuses names outside the book and records the attempt.
  async function submit(event) {
    event.preventDefault();
    const q=query.trim();
    if(!q)return;
    if(shown.length===1){setSelected(shown[0].customer_id);return;}
    if(shown.length)return;
    try {await rmGet(`/book?q=${encodeURIComponent(q)}`,await token());}catch{}
    setNotice('Only clients in your book can be searched. This search was recorded.');
  }
  if(selected)return <ClientOverview customerId={selected} token={token} onAsk={onAsk} onBack={()=>setSelected(null)}/>;
  return <div className="app-page-body rmw-page" data-testid="rm-clients">
    <header className="page-header"><div><span className="eyebrow">Relationship manager</span><h1>My clients</h1>
      <p>All clients assigned to you{book?` (${book.length})`:''}. Open a client to see their records, requests and meetings.</p></div></header>
    <form className="rm-search" role="search" onSubmit={submit}><Icon name="search"/>
      <input aria-label="Search your clients" data-testid="rm-client-search" placeholder="Search by client name" value={query} maxLength={80} autoComplete="off"
        onChange={e=>{setQuery(e.target.value);setNotice('');}}/>
      {query&&<button type="button" className="icon-button" aria-label="Clear search" onClick={()=>{setQuery('');setNotice('');}}><Icon name="close"/></button>}</form>
    {error&&<p role="alert">{error}</p>}
    {!book&&!error&&<p className="loading" role="status">Loading your book…</p>}
    {book&&query&&!shown.length&&<p className="chat-notice-bar" role="status"><Icon name="lock"/><span>{notice||`No client in your book matches “${query.trim()}”. Press Enter to check; names outside your book are never shown.`}</span></p>}
    <ul className="rm-book">{shown.map(c=><li key={c.customer_id}><button type="button" data-testid="rm-client" onClick={()=>setSelected(c.customer_id)}>
      <span className="session-avatar initials" aria-hidden="true">{initials(c.name)}</span>
      <span className="rm-book-main"><strong>{c.name}</strong><small>{c.income_range} · client since {formatDate(c.assigned_since)}</small>
        <span className="rm-book-tags">{c.inherited_from&&<span className="rmw-chip">Inherited from {display(c.inherited_from)}</span>}
          {c.urgent&&<span className="rmw-chip alert">Urgent ticket</span>}{c.overdue>0&&<span className="rmw-chip alert">{c.overdue} past target</span>}
          {c.open_rm_tickets>0&&<span className="rmw-chip info">{c.open_rm_tickets} for you</span>}
          {c.open_specialist_tickets>0&&<span className="rmw-chip">{c.open_specialist_tickets} with specialists</span>}</span></span>
      <span className="rm-book-side"><small>Next meeting</small><strong>{c.next_appointment||'None booked'}</strong>{c.review_due&&<small>Review due {formatDate(c.review_due)}</small>}</span>
      <Icon name="chevron"/>
    </button></li>)}</ul>
  </div>;
}

function Card({title,icon,source,asOf,children,empty,action}) {
  return <section className="rmw-card"><div className="rmw-card-head"><h3><Icon name={icon}/>{title}</h3>{action}</div>
    {empty?<p className="rm-none">{empty}</p>:children}
    <p className="rmw-card-source">Source: {source}{asOf?` · as of ${new Date(asOf).toLocaleTimeString('en-SG',{hour:'numeric',minute:'2-digit'})}`:''}</p></section>;
}

const TABS=[['overview','Overview'],['requests','Requests & tickets'],['meetings','Meetings & notes']];

export function ClientOverview({customerId,token,onAsk,onBack}) {
  const [data,setData]=useState(null),[asOf,setAsOf]=useState(null),[error,setError]=useState(''),[tab,setTab]=useState('overview');
  useEffect(()=>{
    const controller=new AbortController();
    (async()=>{try{const r=await rmGet(`/clients/${customerId}`,await token(),controller.signal);setData(r.data);setAsOf(r.as_of);}
      catch(failure){if(failure.name!=='AbortError')setError(failure.message);}})();
    return ()=>controller.abort();
  },[customerId]);  // eslint-disable-line react-hooks/exhaustive-deps
  const ask=(message,intent,extra={})=>onAsk(message,{intent,context:{client_id:customerId,...extra}});
  const back=<button className="link-button breadcrumb-link" onClick={onBack}>← My clients</button>;
  if(error)return <div className="app-page-body rmw-page">{back}<p role="alert">{error}</p></div>;
  if(!data)return <div className="app-page-body rmw-page">{back}<p className="loading" role="status">Loading the client’s records…</p></div>;
  const p=data.profile,rel=data.relationship||{},mine=data.tickets.filter(t=>t.rm_owned),theirs=data.tickets.filter(t=>!t.rm_owned);
  const next=data.appointments.find(a=>a.status==='confirmed');
  return <div className="app-page-body rmw-page" data-testid="rm-client-overview">
    {back}
    <header className="rmw-client-head">
      <span className="rmw-avatar" aria-hidden="true">{initials(p.name)}</span>
      <div className="rmw-client-title"><h1>{p.name}</h1>
        <div className="rm-book-tags"><span className="rmw-chip">{words(p.employment_status)}</span><span className="rmw-chip">{p.income_range}</span>
          <span className="rmw-chip">Your client since {formatDate(rel.assigned_since)}</span>
          {rel.inherited&&<span className="rmw-chip info">Inherited from {rel.previous_advisor}</span>}</div></div>
      <div className="rmw-client-actions">
        <button className="bank-chat-logout" onClick={()=>ask(`Draft a message to ${p.name}`,'draft_message')}><Icon name="chat"/>Draft a message</button>
        <button className="bank-chat-logout" data-testid="rm-document-call" onClick={()=>onAsk(`Notes from my call today with ${p.name}: `,{context:{client_id:customerId},prefill:true})}><Icon name="document"/>Document a call</button>
        {rel.inherited&&<button className="bank-chat-logout" data-testid="rm-handover" onClick={()=>ask(`Prepare a handover brief for ${p.name}`,'handover_brief')}><Icon name="people"/>Handover brief</button>}
        {next?<button className="primary-button" onClick={()=>ask(`Brief me for my meeting with ${p.name} on ${next.when}`,'meeting_brief',{appointment_id:next.appointment_id})}><Icon name="spark"/>Prepare briefing</button>
          :<button className="primary-button" onClick={()=>ask(`Give me an overview of ${p.name}`,'client_overview')}><Icon name="spark"/>Ask the assistant</button>}
      </div>
    </header>
    <nav className="page-tabs rmw-client-tabs" role="tablist">{TABS.map(([key,label])=><button key={key} role="tab" aria-selected={tab===key} onClick={()=>setTab(key)}>{label}
      {key==='requests'&&data.tickets.length>0&&<span className="count">{data.tickets.length}</span>}</button>)}</nav>
    {data.gaps.length>0&&<p className="rmw-note"><Icon name="info"/>Not on record: {data.gaps.map(g=>GAP_WORDS[g]||words(g)).join(' · ')}.</p>}

    {tab==='overview'&&<div className="rmw-overview">
      <div className="rmw-grid">
        <Card title="Client profile" icon="user" source="client profile" asOf={asOf}>
          <dl className="rmw-kv">
            <div><dt>Employment</dt><dd>{words(p.employment_status)}</dd></div>
            <div><dt>Income range</dt><dd>{p.income_range}</dd></div>
            <div><dt>Language</dt><dd>{p.preferred_language==='en'?'English':p.preferred_language}</dd></div>
            <div><dt>Relationship</dt><dd>{rel.inherited?`Inherited from ${rel.previous_advisor} on ${formatDate(rel.previous_to)}`:`With you since ${formatDate(rel.assigned_since)}`}</dd></div>
          </dl></Card>
        <Card title="Accounts and cards" icon="bank" source="banking records" asOf={asOf} empty={!data.accounts.length&&!data.cards.length&&!data.loans.length&&'None recorded'}>
          <ul className="rm-rows">{data.accounts.map(a=><li key={a.number}><span>{words(a.type)} account {a.number}</span><strong>{formatMoney(Number(a.balance),a.currency)}</strong></li>)}
            {data.cards.map(c=><li key={c.last4}><span>{c.name} ••{c.last4}{c.status!=='active'?` · ${c.status}`:''}</span><strong>{formatMoney(Number(c.outstanding_balance),c.currency)}<small> of {formatMoney(Number(c.credit_limit),c.currency)}</small></strong></li>)}
            {data.loans.map((l,i)=><li key={i}><span>{words(l.type)} loan</span><strong>{formatMoney(Number(l.outstanding_amount),l.currency)}</strong></li>)}</ul></Card>
        <Card title="Coming up" icon="calendar" source="appointments, reviews, maturities and your tasks" asOf={asOf}
          empty={!data.appointments.length&&!data.compliance&&!data.milestones.length&&!data.tasks.length&&'Nothing coming up'}>
          <ul className="rm-rows">{data.appointments.map(a=><li key={a.appointment_id}><span>{a.status==='held'?'Held (not confirmed)':'Meeting'} · {FORMAT_WORDS[a.format]}{a.purpose?` · ${a.purpose}`:''}</span><strong>{a.when}</strong></li>)}
            {data.compliance&&<li><span>{data.compliance.title} · {data.compliance.outstanding_items.length} item(s) to send</span><strong>Due {data.compliance.due_text}</strong></li>}
            {data.milestones.map((m,i)=><li key={i}><span>{m.title}{m.amount?` · ${formatMoney(Number(m.amount),m.currency)}`:''}</span><strong>{m.due_text}</strong></li>)}
            {data.tasks.map(t=><li key={t.task_id}><span>Your task: {t.title}</span><strong className={t.overdue?'rm-overdue':''}>{t.due_text||'No due date'}{t.overdue?' · overdue':''}</strong></li>)}</ul></Card>
        <Card title="What the client has told us" icon="chat" source="client-stated preferences" asOf={asOf} empty={!data.preferences.length&&'Nothing recorded'}>
          <ul className="rmw-quotes">{data.preferences.map((f,i)=><li key={i}><q>{f.preference}</q><small>{f.recorded_text}</small></li>)}</ul>
          {data.shared_note_available&&<p className="rm-small"><Icon name="lock"/>A handover note is shared with you under the client’s consent. A briefing will use it.</p>}</Card>
      </div>
      <AiOverview name={p.name} customerId={customerId} token={token} onAsk={onAsk}/>
    </div>}

    {tab==='requests'&&<div className="rmw-grid two">
      <Card title="Requests for you" icon="clip" source="tickets" asOf={asOf} empty={!mine.length&&!data.rm_requests.length&&'No open requests for you'}>
        <ul className="rmw-list">{mine.map(t=><li key={t.reference}><div><strong>{t.summary}</strong><small><code>{t.reference}</code> · {STATUS_WORDS[t.status]}{t.overdue?' · past target':''}</small></div>
          <div className="rmw-list-actions"><button className="link-button" onClick={()=>ask(`Draft a reply to ${p.name} about ${t.reference}`,'draft_message',{ticket_ref:t.reference})}>Draft a reply</button>
            <button className="link-button" onClick={()=>ask(`Summarise ticket ${t.reference}`,'ticket_summary',{ticket_ref:t.reference})}>Summarise</button></div></li>)}
          {data.rm_requests.map((r,i)=><li key={i}><div><strong>{r.what}</strong><small>Raised with {r.raised_with} · {words(r.status)}</small></div></li>)}</ul></Card>
      <Card title="With specialist teams (read-only)" icon="shield" source="tickets, client-safe status" asOf={asOf} empty={!theirs.length&&'None open'}>
        <ul className="rmw-list">{theirs.map(t=><li key={t.reference}><div><strong>{t.category_label} · {t.owner_team}</strong>
          <small><code>{t.reference}</code> · Client sees: {t.client_safe_status}{t.restricted?' · details restricted':''}</small></div></li>)}</ul></Card>
    </div>}

    {tab==='meetings'&&<div className="rmw-grid two">
      <Card title="Upcoming meetings" icon="calendar" source="appointments" asOf={asOf} empty={!data.appointments.length&&'No meeting booked'}>
        <ul className="rmw-list">{data.appointments.map(a=><li key={a.appointment_id}><div><strong>{a.when}</strong><small>{FORMAT_WORDS[a.format]} · {a.status==='held'?'held, not confirmed':'confirmed'} · {a.purpose||'no purpose recorded'}</small></div>
          {a.status==='confirmed'&&<div className="rmw-list-actions"><button className="link-button" onClick={()=>ask(`Brief me for my meeting with ${p.name} on ${a.when}`,'meeting_brief',{appointment_id:a.appointment_id})}>Prepare briefing</button></div>}</li>)}</ul></Card>
      <Card title="Meeting notes" icon="document" source="meeting notes you may see" asOf={asOf} empty={!data.interactions.length&&'No meeting notes recorded'}
        action={data.interactions.length>0&&<button className="link-button" onClick={()=>ask(`Summarise past meetings with ${p.name}`,'meeting_history')}>Summarise all</button>}>
        <ul className="rmw-list rmw-notes">{data.interactions.map(n=><NoteRow key={n.note_id} note={n} onAsk={(message,intent)=>ask(message,intent,{note_id:n.note_id})}/>)}</ul></Card>
    </div>}
  </div>;
}

// A meeting note opens in place: summary, decisions, commitments and requests as recorded (no model involved).
function NoteRow({note:n,onAsk}) {
  const [open,setOpen]=useState(false);
  const [day,month,year]=(n.met_on_text||'').split(' ');
  const block=(label,items)=>items?.length>0&&<section><h5>{label}</h5><ul>{items.map((x,i)=><li key={i}>{x}</li>)}</ul></section>;
  return <li className={`rmw-note ${open?'open':''}`}>
    <button type="button" className="rmw-note-head" aria-expanded={open} onClick={()=>setOpen(!open)}>
      <span className="rmw-note-date" aria-hidden="true"><strong>{day}</strong><small>{month} {year}</small></span>
      <span className="rmw-note-title"><strong>{n.title||FORMAT_WORDS[n.format]}</strong>
        <small><Icon name={n.format==='phone'?'support':n.format==='video'?'chat':'pin'}/>{FORMAT_WORDS[n.format]} · {n.written_by_current_rm?'your note':`by ${n.author} (previous RM)`}</small></span>
      <Icon name="chevron"/></button>
    {open&&<div className="rmw-note-body" data-testid="rm-note-detail">
      {n.summary?<p className="rmw-note-summary">{n.summary}</p>:<p className="rm-none">No summary recorded.</p>}
      {block('Decisions',n.decisions)}{block('Commitments',n.commitments)}{block('Client requests',n.client_requests)}
      <div className="rmw-note-actions">
        <button type="button" className="rmw-mini" onClick={()=>onAsk(`Summarise my ${n.met_on_text} meeting with this client`,'meeting_history')}><Icon name="spark"/>Ask about this note</button>
        {n.commitments?.length>0&&<button type="button" className="rmw-mini" onClick={()=>onAsk(`Turn the ${n.met_on_text} note into follow-up tasks`,'actions_from_note')}><Icon name="check"/>Turn into tasks</button>}</div>
    </div>}
  </li>;
}

// An AI overview generated on request (never automatically), from the same records; shown as a short report.
function AiOverview({name,customerId,token,onAsk}) {
  const [reply,setReply]=useState(null),[busy,setBusy]=useState(false),[status,setStatus]=useState(''),[error,setError]=useState('');
  const request=useRef(null);
  useEffect(()=>()=>request.current?.abort(),[]);
  async function generate() {
    setBusy(true);setError('');setStatus('Reading the records…');
    const controller=new AbortController();request.current=controller;
    try {
      const message=`Give me an overview of ${name}`;
      setReply(await askAssistant({message,history:[],intent:'client_overview',context:requestContext({client_id:customerId})},await token(),setStatus,controller.signal));
    } catch(failure) {if(failure.name!=='AbortError')setError(failure.message);}
    finally {setBusy(false);setStatus('');}
  }
  return <section className="rmw-card rmw-ai" data-testid="rm-ai-overview">
    <div className="rmw-card-head"><h3><Icon name="spark"/>AI client overview</h3>
      {reply&&<button className="link-button" disabled={busy} onClick={generate}><Icon name="refresh"/>Refresh</button>}</div>
    {!reply&&!busy&&<><p className="rm-small">A summary of {name}’s records with sources, open matters and gaps. Generated only when you ask.</p>
      <button className="primary-button" onClick={generate}><Icon name="spark"/>Generate overview</button></>}
    {busy&&<p className="answer-text typing" role="status"><span className="typing-dots" aria-hidden="true"><i/><i/><i/></span>{status}</p>}
    {error&&<p role="alert" className="rm-error">{error}</p>}
    {reply&&!busy&&<><RmAnswer reply={reply} client={name} compact latest={false} token={token}/>
      <button className="bank-chat-logout" onClick={()=>onAsk(`Give me an overview of ${name}`,{intent:'client_overview',context:{client_id:customerId}})}>Continue in the assistant<Icon name="arrow"/></button></>}
  </section>;
}
