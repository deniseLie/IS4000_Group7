import {useEffect,useRef,useState} from 'react';
import Icon from './Icon';
import {supabase} from '../lib/supabase';
import {prepareTicketAction} from '../lib/ticketWorkflow';
import {latestUpdate,loadCategories,loadTickets,nextStep,requestFailed,STATUSES,ticketSummary,ticketTopic} from '../lib/ticketData';
import {formatDate,formatDateTime} from '../lib/format';
import {possibleFraud,ticketIssues,ticketParts,ticketRows,ticketTitle} from '../lib/requestText';
import RequestField,{CARD_RULES} from './RequestField';
import {loadCardCharges} from '../lib/customerData';

async function token() {
  const {data,error}=await supabase.auth.getSession();if(error)throw error;
  if(!data.session)throw new Error('Please sign in again to see your requests.');
  return data.session.access_token;
}
const TABS=[['open','Open',t=>['submitted','in_review','waiting_on_client'].includes(t.status)],['done','Resolved',t=>['resolved','closed'].includes(t.status)],['all','All',()=>true]];
const tone=t=>t.status==='waiting_on_client'?'urgent':t.overdue?'late':['resolved','closed'].includes(t.status)?'clear':'open';
const SHOWN=10;

// My requests (TK1/TK2): every ticket with its reference and plain-language status, plus a guided form (CL1)
// that proposes a ticket.create action. Nothing is recorded until the customer confirms it in Actions.
export default function TicketsPage({prefill,onReviewActions,onSupport,rmName,initialTab='open'}) {
  const [tickets,setTickets]=useState(null);
  const [categories,setCategories]=useState(null);
  const [tab,setTab]=useState(initialTab==='meetings'?'open':initialTab);  // Meetings has its own page now
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const [raising,setRaising]=useState(!!prefill&&!prefill.tab),[pick,setPick]=useState(null),[all,setAll]=useState(false);
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
  // One row per request: the same request sent more than once (same type and words) is one row; the oldest leads.
  const rows=ticketRows(shown).sort((a,b)=>(tone(a.lead)==='urgent'?0:1)-(tone(b.lead)==='urgent'?0:1)||(b.overdue-a.overdue)||Date.parse(b.lead.created_at)-Date.parse(a.lead.created_at));
  const visible=all?rows:rows.slice(0,SHOWN),selected=rows.find(r=>r.key===pick)||rows[0]||null;
  const open=(tickets||[]).filter(TABS[0][2]),late=ticketRows(open).filter(r=>r.overdue).length,waiting=ticketRows(open).filter(r=>r.lead.status==='waiting_on_client').length;
  return <div className="app-page-body cl-requests" data-testid="tickets-page">
    <header className="page-header"><div><span className="eyebrow">Support</span><h1>Requests</h1>
      <p>{!tickets?'Reading your requests…':open.length?`${ticketRows(open).length} open${waiting?` · ${waiting} need${waiting===1?'s':''} your answer`:''}${late?` · ${late} past the promised time`:''}. Meetings are on the Meetings page.`:'Nothing open right now. Meetings are on the Meetings page.'}</p></div>
      <div className="page-header-actions"><button className="icon-button" aria-label="Refresh" title="Refresh" onClick={refresh} disabled={busy}><Icon name="refresh"/></button>
        <button className="primary-button" data-testid="raise-request" onClick={()=>setRaising(true)}><Icon name="plus"/>Raise a request</button></div></header>
    {raising&&categories&&<RaiseRequest categories={categories.filter(c=>c.customer_raisable)} prefill={prefill} onClose={()=>setRaising(false)} onReviewActions={onReviewActions} onSupport={onSupport}/>}
    <div className="act-chips cl-tabs" role="tablist" aria-label="Show">{TABS.map(([key,label,keep])=><button key={key} type="button" role="tab" data-testid={`ticket-tab-${key}`} aria-selected={tab===key} className={`mtg-chip ${tab===key?'on':''}`} onClick={()=>{setTab(key);setPick(null);}}>{label} · {ticketRows((tickets||[]).filter(keep)).length}</button>)}</div>
    {busy&&!tickets&&<p className="loading" role="status">Loading your requests…</p>}
    {error&&<p role="alert">{error}</p>}
    {tickets&&!rows.length&&<p className="page-empty">No requests here.</p>}
    {rows.length>0&&<div className="rm-inbox-grid">
      <section className="rmw-card rm-inbox-list" aria-label="Your requests">
        <ul className="ticket-list tab-fade cl-rows" key={`list-${tab}`}>{visible.map(r=>{const t=r.lead,on=selected?.key===r.key;return <li key={r.key} className={`rm-inbox-row ${on?'on':''}`} data-testid="ticket" data-reference={t.reference} data-status={t.status}>
          <button type="button" className="rm-inbox-pick" aria-pressed={on} onClick={()=>setPick(r.key)}>
            <span className={`rm-dot ${tone(t)}`} aria-hidden="true"/>
            <span className="rm-now-text"><strong>{ticketTitle(t,'you').replace(/^./,c=>c.toUpperCase())}</strong>
              <small className="rmw-why">{[r.count>1&&`${r.count} requests like this`,STATUSES[t.status],t.overdue&&'past the promised time',`raised ${formatDate(t.created_at)}`,t.reference].filter(Boolean).join(' · ')}</small></span></button>
        </li>;})}</ul>
        {rows.length>SHOWN&&<p className="rm-inbox-more">Showing {visible.length} of {rows.length} · <button type="button" className="link-button" onClick={()=>setAll(o=>!o)}>{all?'Show fewer':'Show all'}</button></p>}
      </section>
      {selected&&<RequestPanel key={selected.key} row={selected} onSupport={onSupport}/>}
    </div>}
  </div>;
}

// The selected request: what she asked, what the bank has told her, who handles it and what happens next.
function RequestPanel({row,onSupport}) {
  const t=row.lead,{words,cards,lead}=ticketParts(t.summary),issues=ticketIssues(t);
  return <aside className="rmw-card rm-inbox-panel" aria-label="Selected request" data-testid="ticket-detail">
    <div className="rm-panel-head"><span className="eyebrow">{t.category_label}</span><span className={`ticket-status ${t.status}${t.overdue?' overdue':''}`}>{STATUSES[t.status]}{t.overdue?' · past the promised time':''}</span></div>
    <h3>{ticketTitle(t,'you').replace(/^./,c=>c.toUpperCase())}</h3>
    <p className="rm-small">Reference <code>{t.reference}</code> · raised {formatDateTime(t.created_at)}{t.last_update?` · updated ${formatDateTime(t.last_update.at)}`:''}</p>
    {possibleFraud(t)&&<p className="rm-fraud" role="note"><strong>Keep your card safe.</strong> If your card isn’t frozen yet, freeze it in Home › Cards, and never share codes with anyone.</p>}
    <div className="rm-panel-part"><span className="eyebrow">You wrote</span><p>{words?`“${words}”`:ticketSummary(t)||lead}</p></div>
    {issues.length>1&&<span className="rm-issues">{issues.map(x=><span key={x}>{x}</span>)}</span>}
    {cards&&<div className="rm-panel-part"><span className="eyebrow">Cards on record</span><p>{cards}</p></div>}
    {t.details?.charge&&<div className="rm-panel-part" data-testid="ticket-charge"><span className="eyebrow">Charge</span><p>{t.details.charge}</p></div>}
    {t.details?.card_frozen&&<div className="rm-panel-part"><span className="eyebrow">Card</span><p>Ending {t.details.card}: {t.details.card_frozen.toLowerCase()}</p></div>}
    {t.attachments.length>0&&<div className="rm-panel-part" data-testid="ticket-attachments"><span className="eyebrow">Attached</span><p>{t.attachments.map(item=>item.file_name).join(', ')}</p></div>}
    <div className="rm-panel-part" data-testid="ticket-update"><span className="eyebrow">What the bank told you</span><p>{latestUpdate(t)||t.last_update?.note||'Nothing yet.'}</p></div>
    <div className="rm-panel-part"><span className="eyebrow">Who handles it</span><p>{t.owner_team}</p></div>
    {nextStep(t)!==(latestUpdate(t)||t.last_update?.note)&&<div className="rm-panel-part"><span className="eyebrow">What happens next</span><p className="ticket-next">{nextStep(t)}</p></div>}
    {row.same.length>0&&<p className="rm-small">You sent this {row.count} times ({[t,...row.same].map(x=>x.reference).join(', ')}). One answer covers all of them.</p>}
    {(t.status==='waiting_on_client'||t.overdue)&&onSupport&&<div className="rm-panel-actions"><button className="bank-chat-logout" onClick={onSupport}>Talk to a person</button></div>}
  </aside>;
}

export function RaiseRequest({categories,prefill,onClose,onReviewActions,onSupport,inChat=false}) {
  const [category,setCategory]=useState(()=>categories.find(c=>c.category===prefill?.category)?.category||categories[0]?.category||'');
  const [values,setValues]=useState(()=>prefill?.details||{});
  const [summary,setSummary]=useState(prefill?.summary||'');
  const [urgency,setUrgency]=useState('normal');
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [outcome,setOutcome]=useState(null);
  const [rule,setRule]=useState(false);  // the bank's rule refused it: change the details, no retry or hand-off needed
  const chosen=categories.find(c=>c.category===category);
  // A dispute names one of her card charges: they are read once, when such a form is open (0096).
  const [charges,setCharges]=useState(null);
  const wantsCharges=!!chosen?.fields.some(field=>field.kind==='transaction');
  useEffect(()=>{
    if(!wantsCharges||charges)return;
    const controller=new AbortController();
    token().then(auth=>loadCardCharges(auth,controller.signal)).then(rows=>{if(!controller.signal.aborted)setCharges(rows);}).catch(()=>{if(!controller.signal.aborted)setCharges([]);});
    return ()=>controller.abort();
  },[wantsCharges]);  // eslint-disable-line react-hooks/exhaustive-deps
  const needsUntil=category==='card_limit_change'&&values.duration==='temporary';
  async function prepare(event) {
    event.preventDefault();setBusy(true);setError('');
    try {
      const auth=await token();
      setOutcome(await prepareTicketAction({category:chosen,summary,urgency,values},auth));
    } catch(failure) {setError(failure.code==='action_rule'?failure.message:requestFailed(failure));setRule(failure.code==='action_rule');} finally {setBusy(false);}
  }
  const locked=busy||!!outcome;
  return <section className="relationship-details raise-request" data-testid="raise-request-form" aria-label="Raise a request">
    <h3>Raise a request</h3>
    <form className="records-filters" onSubmit={prepare}>
      <label>What is it about? <select data-testid="ticket-category" value={category} disabled={locked} onChange={event=>{setCategory(event.target.value);setValues({});}}>
        {categories.map(c=><option key={c.category} value={c.category}>{c.label}</option>)}</select></label>
      {chosen&&<p>{chosen.description} <strong>Who decides:</strong> {chosen.decided_by}{chosen.documents.length?<> <strong>You may need:</strong> {chosen.documents.join('; ')}.</>:null}</p>}
      {prefill?.selectedCardLabel&&category==='card_limit_change'&&<p>Selected in chat: <strong>{prefill.selectedCardLabel}</strong>. Enter the card’s last four digits to verify the card before submitting.</p>}
      {CARD_RULES[category]&&<p className="request-rules" data-testid="request-rules"><Icon name="info"/>{CARD_RULES[category]}</p>}
      {chosen?.fields.map(field=><RequestField key={field.key} field={field} value={values[field.key]||''} disabled={locked} testId={`ticket-field-${field.key}`}
        required={field.required||field.key==='until'&&needsUntil} charges={charges} card={values.card}
        className={field.kind==='confirm'?'request-details-field':''}
        onChange={value=>setValues({...values,[field.key]:value,...(field.key==='duration'&&value==='permanent'?{until:''}:{}),...(field.key==='card'&&values.transaction?{transaction:''}:{})})}/>)}
      <label className="request-details-field">Short summary <textarea data-testid="ticket-summary" required maxLength={500} value={summary} disabled={locked} onChange={event=>setSummary(event.target.value)}/></label>
      <label>Urgency <select value={urgency} disabled={locked} onChange={event=>setUrgency(event.target.value)}>{['low','normal','high'].map(value=><option key={value}>{value}</option>)}</select></label>
      <p>Never type a password, PIN, one-time code or full card number. You review and explicitly confirm the exact request before it is recorded.</p>
      <div className="panel-actions"><button type="button" className="bank-chat-logout" onClick={onClose} disabled={busy}>Cancel</button>
        <button className="primary-button" data-testid="prepare-ticket" disabled={locked||!summary.trim()}>Review request</button></div>
    </form>
    {busy&&<p className="loading" role="status">Preparing your request for review…</p>}
    {error&&rule&&<p role="alert" className="request-rule-error" data-testid="ticket-rule"><Icon name="info"/>{error} Change the details above and review again.</p>}
    {error&&!rule&&<div role="alert" data-testid="ticket-error"><p>{error}</p>
      <div className="panel-actions"><button type="button" className="bank-chat-logout" onClick={prepare} disabled={busy||!summary.trim()}>Try again</button>
        {onSupport&&<button type="button" className="primary-button" data-testid="ticket-error-support" onClick={onSupport}>Talk to a person</button>}</div></div>}
    {outcome&&<div data-testid="ticket-proposal"><p>Ready for review. Confirm or reject the exact details; nothing is recorded until you confirm.</p>
      <button className="primary-button" data-testid="review-ticket-actions" onClick={onReviewActions}>{inChat?"Review and confirm here":"Review in Actions"}</button></div>}
  </section>;
}
