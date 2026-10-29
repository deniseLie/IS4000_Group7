import {useEffect,useRef,useState} from 'react';
import Icon from './Icon';
import {supabase} from '../lib/supabase';
import {createActionCase,proposeAction} from '../lib/actions';
import {loadCategories,loadTickets,nextStep,STATUSES,ticketSummary} from '../lib/ticketData';
import {formatDateTime} from '../lib/format';

async function token() {
  const {data,error}=await supabase.auth.getSession();if(error)throw error;
  if(!data.session)throw new Error('Please sign in again to see your requests.');
  return data.session.access_token;
}
const TABS=[['open','Open',t=>['submitted','in_review','waiting_on_client'].includes(t.status)],['done','Resolved',t=>['resolved','closed'].includes(t.status)],['all','All',()=>true]];

// My requests (TK1/TK2): every ticket with its reference and plain-language status, plus a guided form (CL1)
// that proposes a ticket.create action. Nothing is recorded until the customer confirms it in Actions.
export default function TicketsPage({prefill,onReviewActions,onSupport}) {
  const [tickets,setTickets]=useState(null);
  const [categories,setCategories]=useState(null);
  const [tab,setTab]=useState('open');
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const [raising,setRaising]=useState(!!prefill);
  const active=useRef(null);
  async function refresh() {
    active.current?.abort();const controller=new AbortController();active.current=controller;setBusy(true);setError('');
    try {
      const auth=await token();
      const [rows,cats]=await Promise.all([loadTickets(auth,controller.signal),categories?Promise.resolve(categories):loadCategories(auth,controller.signal)]);
      if(!controller.signal.aborted){setTickets(rows);setCategories(cats);}
    } catch(failure) {if(failure.name!=='AbortError'&&!controller.signal.aborted)setError(failure.message);}
    finally {if(!controller.signal.aborted)setBusy(false);}
  }
  useEffect(()=>{refresh();return ()=>active.current?.abort();},[]);  // eslint-disable-line react-hooks/exhaustive-deps
  const shown=(tickets||[]).filter(TABS.find(entry=>entry[0]===tab)[2]);
  return <div className="app-page-body" data-testid="tickets-page">
    <header className="page-header"><div><span className="eyebrow">Support</span><h1>My requests</h1>
      <p>Requests you have raised with the bank and their current status.</p></div>
      <div className="page-header-actions"><button className="bank-chat-logout" onClick={refresh} disabled={busy}><Icon name="refresh"/>Refresh</button>
        <button className="primary-button" data-testid="raise-request" onClick={()=>setRaising(true)}><Icon name="plus"/>Raise a request</button></div></header>
    {raising&&categories&&<RaiseRequest categories={categories.filter(c=>c.customer_raisable)} prefill={prefill} onClose={()=>setRaising(false)} onReviewActions={onReviewActions}/>}
    <nav className="page-tabs" role="tablist">{TABS.map(([key,label,keep])=><button key={key} role="tab" aria-selected={tab===key} onClick={()=>setTab(key)}>{label}<span className="count">{(tickets||[]).filter(keep).length}</span></button>)}</nav>
    {busy&&!tickets&&<p className="loading" role="status">Loading your requests…</p>}
    {error&&<p role="alert">{error}</p>}
    {tickets&&!shown.length&&<p className="page-empty">No requests here.</p>}
    <ul className="ticket-list tab-fade" key={tab}>{shown.map(ticket=><li key={ticket.reference} className="relationship-details" data-testid="ticket" data-reference={ticket.reference} data-status={ticket.status}>
      <div className="ticket-head"><strong>{ticket.category_label}</strong><span className={`ticket-status ${ticket.status}${ticket.overdue?' overdue':''}`}>{STATUSES[ticket.status]}{ticket.overdue?' · past target time':''}</span></div>
      <p>{ticketSummary(ticket)}</p>
      <p className="ticket-next"><Icon name="info"/>{nextStep(ticket)}</p>
      <small>Reference <code>{ticket.reference}</code> · {ticket.owner_team} · raised {formatDateTime(ticket.created_at)}{ticket.last_update?` · updated ${formatDateTime(ticket.last_update.at)}`:''}</small>
      {ticket.status==='waiting_on_client'&&onSupport&&<p><button className="bank-chat-logout" onClick={onSupport}>Talk to a person</button></p>}
    </li>)}</ul>
    <p className="page-note"><Icon name="shield"/>Demo service: references and statuses are synthetic. No real bank team works these requests.</p>
  </div>;
}

function RaiseRequest({categories,prefill,onClose,onReviewActions}) {
  const [category,setCategory]=useState(()=>categories.find(c=>c.category===prefill?.category)?.category||categories[0]?.category||'');
  const [values,setValues]=useState({});
  const [summary,setSummary]=useState(prefill?.summary||'');
  const [urgency,setUrgency]=useState('normal');
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [outcome,setOutcome]=useState(null);
  const chosen=categories.find(c=>c.category===category);
  async function prepare(event) {
    event.preventDefault();setBusy(true);setError('');
    try {
      const auth=await token();
      const scope=await createActionCase({session_id:crypto.randomUUID(),case_id:crypto.randomUUID()},auth);
      const details=Object.fromEntries(Object.entries(values).filter(([name,value])=>chosen.fields.some(f=>f.key===name)&&String(value).trim()));
      setOutcome(await proposeAction({command_id:crypto.randomUUID(),case_id:scope.case_id,task_id:'ticket',type:'ticket.create',target:{category,summary,urgency,details}},auth));
    } catch(failure) {setError(failure.message);} finally {setBusy(false);}
  }
  const locked=busy||!!outcome;
  return <section className="relationship-details raise-request" data-testid="raise-request-form" aria-label="Raise a request">
    <h3>Raise a request</h3>
    <form className="records-filters" onSubmit={prepare}>
      <label>What is it about? <select data-testid="ticket-category" value={category} disabled={locked} onChange={event=>{setCategory(event.target.value);setValues({});}}>
        {categories.map(c=><option key={c.category} value={c.category}>{c.label}</option>)}</select></label>
      {chosen&&<p>{chosen.description} <strong>Who decides:</strong> {chosen.decided_by}{chosen.documents.length?<> <strong>You may need:</strong> {chosen.documents.join('; ')}.</>:null}</p>}
      {chosen?.fields.map(field=><label key={field.key}>{field.label}{field.required?'':' (optional)'}
        {field.kind==='choice'?<select data-testid={`ticket-field-${field.key}`} required={field.required} value={values[field.key]||''} disabled={locked} onChange={event=>setValues({...values,[field.key]:event.target.value})}>
            <option value="">Choose…</option>{field.choices.map(choice=><option key={choice}>{choice}</option>)}</select>
          :<input data-testid={`ticket-field-${field.key}`} required={field.required} disabled={locked} value={values[field.key]||''}
            type={field.kind.startsWith('date')?'date':'text'} inputMode={['amount','card'].includes(field.kind)?'decimal':undefined}
            pattern={field.kind==='card'?'[0-9]{4}':field.kind==='amount'?'[0-9]{1,9}(\\.[0-9]{1,2})?':undefined} maxLength={field.kind==='card'?4:300}
            onChange={event=>setValues({...values,[field.key]:event.target.value})}/>}</label>)}
      <label className="request-details-field">Short summary <textarea data-testid="ticket-summary" required maxLength={500} value={summary} disabled={locked} onChange={event=>setSummary(event.target.value)}/></label>
      <label>Urgency <select value={urgency} disabled={locked} onChange={event=>setUrgency(event.target.value)}>{['low','normal','high'].map(value=><option key={value}>{value}</option>)}</select></label>
      <p>Never type a password, PIN, one-time code or full card number. You review the exact request in Actions before it is recorded.</p>
      <div className="panel-actions"><button type="button" className="bank-chat-logout" onClick={onClose} disabled={busy}>Cancel</button>
        <button className="primary-button" data-testid="prepare-ticket" disabled={locked||!summary.trim()}>Review request</button></div>
    </form>
    {busy&&<p className="loading" role="status">Preparing your request for review…</p>}
    {error&&<p role="alert" data-testid="ticket-error">{error}</p>}
    {outcome&&<div data-testid="ticket-proposal"><p>Ready for review. Confirm or reject the exact details in Actions; nothing is recorded until you confirm.</p>
      <button className="primary-button" data-testid="review-ticket-actions" onClick={onReviewActions}>Review in Actions</button></div>}
  </section>;
}
