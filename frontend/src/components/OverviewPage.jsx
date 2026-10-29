import {useEffect,useState} from 'react';
import Icon from './Icon';
import {initials} from './SharingPage';
import Statements from './Statements';
import {supabase} from '../lib/supabase';
import {loadRecords} from '../lib/customerData';
import {displayName,formatDate,formatDateTime,formatMoney as money} from '../lib/format';
import {cacheOwner,cached,remember} from '../lib/cache';

const ACCOUNT={savings:'Savings account',current:'Current account',multi_currency:'Multi-currency account'};
const LOAN={home:'Home loan',car:'Car loan',personal:'Personal loan',securities_backed:'Securities-backed loan'};
const REVIEW_DONE=['completed','closed'];
const QUERY={from_date:null,to_date:null,filters:null},KEY=`records:${JSON.stringify(QUERY)}`;  // the same unfiltered read Records opens with
function greeting() {const hour=Number(new Date().toLocaleString('en-SG',{timeZone:'Asia/Singapore',hour:'numeric',hour12:false}));return hour<12?'Good morning':hour<18?'Good afternoon':'Good evening';}

// Online-banking style landing page: everything the customer owns at a glance, with one way into each task.
export default function OverviewPage({onNavigate,onAsk,onRequestStatement,focus}) {
  // The last loaded records show at once on a return visit while a fresh read replaces them.
  const [records,setRecords]=useState(()=>cached(KEY)),[error,setError]=useState('');
  useEffect(()=>{
    const controller=new AbortController(),scope=cacheOwner();
    (async()=>{
      try {
        const {data,error:sessionError}=await supabase.auth.getSession();
        if(sessionError)throw sessionError;
        if(!data.session)throw new Error('Please sign in again to view your accounts.');
        const result=await loadRecords(data.session.access_token,QUERY,controller.signal);
        if(!controller.signal.aborted)setRecords(remember(KEY,result,scope));
      } catch(failure) {if(failure.name!=='AbortError'&&!controller.signal.aborted)setError(failure.message);}
    })();
    return ()=>controller.abort();
  },[]);
  // Opened from the assistant's "Go to Statements" button: scroll to that section once the records are on screen.
  useEffect(()=>{if(focus==='statements'&&records)document.getElementById('statements')?.scrollIntoView({behavior:'smooth',block:'start'});},[focus,!!records]);
  if(error&&!records)return <section className="app-page-body ov" data-testid="overview-page"><p role="alert">{error}</p></section>;
  if(!records)return <section className="app-page-body ov" data-testid="overview-page"><p className="loading" role="status">Loading your accounts…</p>
    <div className="skeleton-page" aria-hidden="true"><span className="skeleton-line wide"/><span className="skeleton-block tall"/>
      <div className="ov-grid"><div className="ov-main"><span className="skeleton-block"/><span className="skeleton-block"/></div><div className="ov-side"><span className="skeleton-block"/><span className="skeleton-block"/></div></div></div></section>;
  const name=displayName(records.profile.data.name).split(' ')[0];
  const accounts=records.accounts.data.filter(row=>row.status!=='closed'),cards=records.cards.data,loans=records.loans.data.filter(row=>row.status==='active');
  const sgd=accounts.filter(row=>row.currency==='SGD').reduce((sum,row)=>sum+row.balance,0);
  const rm=records.advisor.data?.current,review=records.compliance.data,open=review&&!REVIEW_DONE.includes(review.status);
  const recent=[...records.transactions.data].sort((a,b)=>b.date.localeCompare(a.date)).slice(0,5);
  return <section className="app-page-body ov" aria-label="Overview" data-testid="overview-page">
    <header className="ov-head"><div><p className="ov-date">{new Date().toLocaleDateString('en-SG',{timeZone:'Asia/Singapore',weekday:'long',day:'numeric',month:'long'})}</p>
      <h1>{greeting()}, {name}</h1></div>
</header>
    {error&&<p role="alert">{error} These are your last loaded records.</p>}
    <div className="ov-balance"><span>Total balance in SGD accounts</span><strong data-testid="overview-total">{money(sgd)}</strong><small>As of {formatDateTime(records.accounts.as_of)}</small></div>
    <div className="ov-grid">
      <div className="ov-main">
        <article className="ov-card"><h2>Accounts</h2>
          {accounts.length?<ul className="ov-rows">{accounts.map(row=><li key={row.account_id}><span className="ov-icon"><Icon name="account"/></span><div><strong>{ACCOUNT[row.type]}</strong><small>{row.number}{row.status==='dormant'?' · Dormant':''}</small></div><b>{money(row.balance,row.currency)}</b></li>)}</ul>
            :<p className="ov-empty">You have no open accounts.</p>}</article>
        {cards.length>0&&<article className="ov-card"><h2>Cards</h2><ul className="ov-rows">{cards.map(row=><li key={row.card_id}><span className="ov-icon"><Icon name="card"/></span>
          <div><strong>{row.name}</strong><small>•••• {row.last4}{row.status!=='active'?` · ${row.status.replaceAll('_',' ')}`:''}{row.due_date?` · Due ${formatDate(row.due_date)}`:''}</small></div>
          <b>{money(row.outstanding)}<small>of {money(row.limit)} limit</small></b></li>)}</ul></article>}
        {loans.length>0&&<article className="ov-card"><h2>Loans</h2><ul className="ov-rows">{loans.map(row=><li key={row.loan_id}><span className="ov-icon"><Icon name="loan"/></span>
          <div><strong>{LOAN[row.type]}</strong><small>Next payment {money(row.next_amount)}{row.next_payment?` on ${formatDate(row.next_payment)}`:''}</small></div><b>{money(row.outstanding)}<small>outstanding</small></b></li>)}</ul></article>}
        <article className="ov-card"><h2>Recent activity</h2>
          {recent.length?<ul className="ov-rows ov-txns">{recent.map(row=><li key={row.txn_id}><div><strong>{row.description}</strong><small>{formatDate(row.date)}{row.card_last4?` · Card •••• ${row.card_last4}`:''}{row.status!=='posted'?` · ${row.status}`:''}</small></div>
            <b className={row.amount<0?'out':'in'}>{row.amount<0?'−':'+'}{money(Math.abs(row.amount),row.currency)}</b></li>)}</ul>
            :<p className="ov-empty">No recent transactions.</p>}
          <button className="source-link" onClick={()=>onAsk('Show my recent transactions')}>Ask about a transaction</button></article>
        <Statements records={records} onRequest={onRequestStatement}/>
      </div>
      <aside className="ov-side">
        <article className="ov-card ov-tile" data-testid="overview-help"><h2>Need a person?</h2>
          <p className="ov-small">Ask the assistant first. If it can't answer, it passes your question to your relationship manager and gives you a reference.</p>
          <button className="bank-chat-logout" data-testid="overview-open-requests" onClick={()=>onNavigate('requests')}>See my requests<Icon name="arrow"/></button></article>
        <article className={`ov-card ov-tile ${open?'attention':''}`} data-testid="overview-compliance"><h2>Compliance review</h2>
          {open?<><p><strong>{review.title}</strong></p><p className="ov-small">Action needed{review.due_date?` by ${formatDate(review.due_date)}`:''}</p>
            <button className="primary-button" onClick={()=>onNavigate('compliance')}>Continue review<Icon name="arrow"/></button></>
            :<><p className="ov-ok"><Icon name="check"/>Nothing needed from you</p><p className="ov-small">You have no open review. We will tell you in your Inbox if the bank needs anything.</p></>}</article>
        <article className="ov-card ov-tile"><h2>Need help?</h2><p className="ov-small">Ask the assistant anything about your accounts, or reach a person.</p>
          <button className="bank-chat-logout" onClick={()=>onNavigate('chat')}>Open the assistant<Icon name="arrow"/></button></article>
      </aside>
    </div>
  </section>;
}
