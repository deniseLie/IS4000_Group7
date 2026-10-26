import ChecklistPreparation from './ChecklistPreparation';
import DocumentRecords from './DocumentRecords';
import {useEffect,useState} from 'react';
import Panel from './Panel';
import {supabase} from '../lib/supabase';
import {loadRecords} from '../lib/customerData';

function label(value) {return value.replaceAll('_',' ');}
function amount(value) {return value.toLocaleString('en-SG',{minimumFractionDigits:2,maximumFractionDigits:2});}
function recordedDate(value) {return value===null?'Not recorded':value;}

export default function RecordsPanel({onClose}) {
  const [records,setRecords]=useState(null);
  const [error,setError]=useState('');
  const [loading,setLoading]=useState(false);
  const [revision,setRevision]=useState(0);
  const [from,setFrom]=useState('');
  const [to,setTo]=useState('');
  const [status,setStatus]=useState('');
  const [query,setQuery]=useState({from_date:null,to_date:null,filters:null});
  useEffect(()=>{
    const controller=new AbortController();
    async function load() {
      setLoading(true);setError('');setRecords(null);
      try {
        const {data,error:sessionError}=await supabase.auth.getSession();
        if(sessionError)throw sessionError;
        if(!data.session)throw new Error('Please sign in again to view your records.');
        const result=await loadRecords(data.session.access_token,query,controller.signal);
        if(!controller.signal.aborted)setRecords(result);
      } catch(failure) {if(failure.name!=='AbortError'&&!controller.signal.aborted)setError(failure.message);}
      finally {if(!controller.signal.aborted)setLoading(false);}
    }
    load();return ()=>controller.abort();
  },[revision,query]);
  function refresh(event) {
    event.preventDefault();
    setQuery({from_date:from||null,to_date:to||null,filters:status?{status}:null});setRevision(value=>value+1);
  }
  const footer=<><span>{records?<>Recorded as of <time dateTime={records.profile.as_of}>{new Date(records.profile.as_of).toLocaleString()}</time>. Each section is a separate audited read.</>:'Synthetic records · read only'}</span><button className="bank-chat-logout" onClick={onClose}>Close</button></>;
  return <Panel label="Customer records" eyebrow="Synthetic records · Read only" title="Your records" closeLabel="Close records" onClose={onClose} size="wide" footer={footer}>
    <p className="panel-notice">Read the synthetic records linked to your sign-in. This view does not change your data. Checklist preparation marks can be saved separately below.</p>
    <form className="records-filters records-filter-bar" onSubmit={refresh}>
      <label>Transactions from<input data-testid="records-from" type="date" value={from} onChange={event=>setFrom(event.target.value)}/></label>
      <label>Transactions to<input data-testid="records-to" type="date" value={to} onChange={event=>setTo(event.target.value)}/></label>
      <label>Status<select data-testid="records-status" value={status} onChange={event=>setStatus(event.target.value)}><option value="">All</option>{['posted','pending','failed'].map(value=><option key={value}>{value}</option>)}</select></label>
      <button className="bank-chat-logout" data-testid="reload-records" disabled={loading}>Reload records</button>
    </form>
    {loading&&<p className="loading" role="status" data-testid="records-loading">Loading recorded data…</p>}
    {error&&<p role="alert" data-testid="records-error">{error}</p>}
    {records&&<div data-testid="records-content" className="customer-records">
      <section className="records-profile"><h3 data-testid="records-name">{records.profile.data.name}</h3><p>{label(records.profile.data.employment_status)} · Preferred language: {records.profile.data.preferred_language}</p></section>
      <section><h3>Accounts</h3>{records.accounts.data.length===0&&<p data-testid="records-accounts-empty">No accounts recorded.</p>}
        {records.accounts.data.map(row=><article key={row.account_id} data-testid="records-account"><h4>{label(row.type)} · {row.number}</h4><p>{row.currency} {amount(row.balance)} · {label(row.status)}</p></article>)}</section>
      <section><h3>Cards</h3>{records.cards.data.length===0&&<p>No cards recorded.</p>}
        {records.cards.data.map(row=><article key={row.card_id} data-testid="records-card"><h4>{row.name} · ****{row.last4}</h4>
          <dl className="sharing-details record-table"><dt>Status</dt><dd>{label(row.status)}</dd><dt>Credit limit</dt><dd>{amount(row.limit)}</dd><dt>Outstanding</dt><dd>{amount(row.outstanding)}</dd><dt>Minimum payment</dt><dd>{amount(row.minimum_payment)}</dd><dt>Due date</dt><dd>{recordedDate(row.due_date)}</dd></dl></article>)}</section>
      <section><h3>Loans</h3>{records.loans.data.length===0&&<p data-testid="records-loans-empty">No loans recorded.</p>}
        {records.loans.data.map(row=><article key={row.loan_id} data-testid="records-loan"><h4>{label(row.type)} · {label(row.status)}</h4>
          <dl className="sharing-details record-table"><dt>Original amount</dt><dd>{amount(row.principal)}</dd><dt>Outstanding</dt><dd>{amount(row.outstanding)}</dd><dt>Next payment</dt><dd>{amount(row.next_amount)}</dd><dt>Due date</dt><dd>{recordedDate(row.next_payment)}</dd></dl></article>)}
        <p className="source-notice">Currency is not recorded for card and loan amounts.</p></section>
      <section><h3>Transactions</h3><p>Latest {records.transactions.data.length} matching records, up to 200. Dates use Singapore time.</p>
        {records.transactions.data.length===0&&<p data-testid="records-transactions-empty">No matching transactions.</p>}
        {records.transactions.data.map(row=><article key={row.txn_id} data-testid="records-transaction"><h4>{row.description}</h4><p>{row.date} · {row.currency} {amount(row.amount)} · {row.status}{row.card_last4&&` · Card ****${row.card_last4}`}{row.is_overseas&&' · Overseas'}</p>{row.failure_reason&&<p>{row.failure_reason}</p>}</article>)}</section>
      <section><h3>Compliance review</h3>{records.compliance.data?<article data-testid="records-case"><h4>{records.compliance.data.title}</h4><p>{records.compliance.data.notice_text}</p><p>{label(records.compliance.data.status)} · Due: {recordedDate(records.compliance.data.due_date)}</p>
        <ul>{records.compliance.data.requested_items.map(item=><li key={item.item_id} data-checklist-item-id={item.item_id}>{item.label} · {label(item.submission_status)} · {label(item.client_mark)}{item.instructions&&<p>{item.instructions}</p>}<ChecklistPreparation review={records.compliance.data} item={item}/></li>)}</ul></article>:<p>No client-visible review recorded.</p>}</section>
      <DocumentRecords snapshot={records.documents} review={records.compliance.data}/>
      <section><h3>Relationship manager</h3>{records.advisor.data?.current?<article data-testid="records-advisor"><h4>{records.advisor.data.current.name}</h4><p>{records.advisor.data.current.role} · {records.advisor.data.current.team}</p><p>Assigned since {records.advisor.data.current.started_on}</p>
        <p data-testid="records-advisor-contact">Email: {records.advisor.data.current.contact.email||'Not recorded'} · Phone: {records.advisor.data.current.contact.phone||'Not recorded'}</p></article>:<p>No current relationship manager recorded.</p>}
        {records.advisor.data?.previous&&<p>Previous: {records.advisor.data.previous.name} · Ended {records.advisor.data.previous.ended_on}</p>}</section>
      <p className="source-notice">Reload for current records.</p>
    </div>}
  </Panel>;
}
