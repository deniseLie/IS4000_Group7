import {useEffect,useState} from 'react';
import Icon from './Icon';
import {initials} from '../lib/format';
import Statements from './Statements';
import CardControls from './CardControls';
import {supabase} from '../lib/supabase';
import {disputableCharges,loadRecords} from '../lib/customerData';
import {displayName,formatDate,formatDateTime,formatMoney as money} from '../lib/format';
import {cacheOwner,cached,remember} from '../lib/cache';
import {reviewNextStep} from '../lib/compliance';
import {loadActions} from '../lib/actions';
import {loadAppointments} from '../lib/relationship';
import {appointmentTime} from '../lib/appointmentData';

const ACCOUNT={savings:'Savings account',current:'Current account',multi_currency:'Multi-currency account'};
const LOAN={home:'Property financing',car:'Car loan',personal:'Personal loan',securities_backed:'Lombard loan (against investments)'};
const ASSET={cash:'Cash deposits',bonds:'Bonds',equities:'Equities',funds:'Funds',alternatives:'Alternatives'};
const REVIEW_DONE=['completed','closed'];
const QUERY={from_date:null,to_date:null,filters:null},KEY=`records:${JSON.stringify(QUERY)}`;  // the same unfiltered read Records opens with
function greeting() {const hour=Number(new Date().toLocaleString('en-SG',{timeZone:'Asia/Singapore',hour:'numeric',hour12:false}));return hour<12?'Good morning':hour<18?'Good afternoon':'Good evening';}

// Online-banking style landing page: everything the customer owns at a glance, with one way into each task.
export default function OverviewPage({onNavigate,onAsk,onRequestStatement,onRaise,focus,onRecords,onSupport}) {
  // The last loaded records show at once on a return visit while a fresh read replaces them.
  const [records,setRecords]=useState(()=>cached(KEY)),[error,setError]=useState('');
  const [reload,setReload]=useState(0),[managing,setManaging]=useState(null);  // 0096: card controls open for one card
  const [extra,setExtra]=useState({actions:[],meetings:[]});
  const [allTxns,setAllTxns]=useState(false),[from,setFrom]=useState(''),[to,setTo]=useState('');  // Recent activity › See all (on this page)  // Home: what is waiting for her (optional reads, never block the page)
  useEffect(()=>{
    const controller=new AbortController(),scope=cacheOwner();
    (async()=>{
      try {
        const {data,error:sessionError}=await supabase.auth.getSession();
        if(sessionError)throw sessionError;
        if(!data.session)throw new Error('Please sign in again to view your accounts.');
        const result=await loadRecords(data.session.access_token,QUERY,controller.signal);
        if(!controller.signal.aborted)setRecords(remember(KEY,result,scope));
        const access=data.session.access_token,quiet=promise=>promise.catch(()=>null);
        const [actions,meetings]=await Promise.all([quiet(loadActions(access,controller.signal)),quiet(loadAppointments(access,controller.signal))]);
        if(!controller.signal.aborted)setExtra({actions:actions||[],meetings:meetings?.data||[]});
      } catch(failure) {if(failure.name!=='AbortError'&&!controller.signal.aborted)setError(failure.message);}
    })();
    return ()=>controller.abort();
  },[reload]);
  // Opened from the assistant's "Go to Statements" button: scroll to that section once the records are on screen.
  // CR2/CR15: a "Go to …" button in the chat scrolls to its section (statements, accounts, cards, loans, recent activity).
  useEffect(()=>{if(focus&&records)document.getElementById(focus==='statements'?'statements':`ov-${focus}`)?.scrollIntoView({behavior:'smooth',block:'start'});},[focus,!!records]);
  if(error&&!records)return <section className="app-page-body ov" data-testid="overview-page"><p role="alert">{error}</p></section>;
  if(!records)return <section className="app-page-body ov" data-testid="overview-page"><p className="loading" role="status">Loading your accounts…</p>
    <div className="skeleton-page" aria-hidden="true"><span className="skeleton-line wide"/><span className="skeleton-block tall"/>
      <div className="ov-grid"><div className="ov-main"><span className="skeleton-block"/><span className="skeleton-block"/></div><div className="ov-side"><span className="skeleton-block"/><span className="skeleton-block"/></div></div></div></section>;
  const name=displayName(records.profile.data.name).split(' ')[0];
  const accounts=records.accounts.data.filter(row=>row.status!=='closed'),cards=records.cards.data,loans=records.loans.data.filter(row=>row.status==='active');
  const portfolio=records.portfolio?.data||null;  // 0116
  const sgd=accounts.filter(row=>row.currency==='SGD').reduce((sum,row)=>sum+row.balance,0);
  const rm=records.advisor.data?.current,review=records.compliance.data,open=review&&!REVIEW_DONE.includes(review.status);
  const reviewStep=open?reviewNextStep(review):null;
  const sorted=[...records.transactions.data].sort((a,b)=>b.date.localeCompare(a.date));
  const recent=allTxns?sorted.filter(row=>(!from||row.date>=from)&&(!to||row.date<=to)):sorted.slice(0,5);
  const disputable=new Set(disputableCharges(records.transactions.data).map(row=>row.txn_id));
  const rmFirst=rm?displayName(rm.name).split(' ')[0]:'your relationship manager';
  // Waiting for you: what she must confirm, documents her review still needs, and her next booked meeting.
  const toConfirm=extra.actions.filter(item=>item.command.state==='proposed');
  const missing=open?review.requested_items.filter(item=>['not_submitted','rejected'].includes(item.submission_status)):[];
  const nextMeeting=extra.meetings.filter(item=>['confirmed','held'].includes(item.status)&&Date.parse(item.ends_at)>Date.now()).sort((a,b)=>Date.parse(a.starts_at)-Date.parse(b.starts_at))[0];
  const waiting=[
    toConfirm.length>0&&{key:'confirm',tone:'urgent',title:toConfirm.length===1?'1 thing to confirm':`${toConfirm.length} things to confirm`,why:'Nothing happens until you confirm it',label:'Review',go:()=>onNavigate('actions')},
    missing.length>0&&{key:'review',tone:'date',title:missing.length===1?`Send your ${missing[0].label.replace(/^./,c=>c.toLowerCase())}`:`Send ${missing.length} documents for your periodic review`,why:`For your periodic review${review.due_date?` · by ${formatDate(review.due_date)}`:''}`,label:'Continue',go:()=>onNavigate('compliance')},
    nextMeeting&&{key:'meeting',tone:'meeting',title:`${nextMeeting.status==='held'?'Confirm your meeting':'Meeting'} with ${rmFirst} · ${appointmentTime(nextMeeting.starts_at)}`,why:nextMeeting.purpose||'With your relationship manager',label:nextMeeting.status==='held'?'Confirm':'Open',go:()=>onNavigate('meetings')}].filter(Boolean);
  return <section className="app-page-body ov" aria-label="Overview" data-testid="overview-page">
    <header className="ov-head"><div><p className="ov-date">{new Date().toLocaleDateString('en-SG',{timeZone:'Asia/Singapore',weekday:'long',day:'numeric',month:'long'})}</p>
      <h1>{greeting()}, {name}</h1></div>
</header>
    {error&&<p role="alert">{error} These are your last loaded records.</p>}
    {waiting.length>0&&<section className="ov-waiting" aria-label="Waiting for you" data-testid="overview-waiting"><div className="ov-waiting-head"><h2>Waiting for you</h2><span>{waiting.length} thing{waiting.length===1?'':'s'}</span></div>
      {waiting.map((x,i)=><div key={x.key} className="ov-waiting-row"><span className={`rm-dot ${x.tone}`} aria-hidden="true"/><span className="ov-waiting-text"><strong>{x.title}</strong><small>{x.why}</small></span>
        <button className={i===0?'primary-button':'bank-chat-logout'} onClick={x.go}>{x.label}</button></div>)}</section>}
    <div className="ov-balance"><div><span>Accounts (SGD)</span><strong data-testid="overview-total">{money(sgd)}</strong></div>
      {portfolio&&<div><span>Investments</span><strong>{money(portfolio.total_value,portfolio.currency)}</strong></div>}
      <small>As of {formatDateTime(records.accounts.as_of)}</small></div>
    <div className="ov-grid">
      <div className="ov-main">
        <article className="ov-card" id="ov-accounts"><h2>Accounts</h2>
          {accounts.length?<ul className="ov-rows">{accounts.map(row=><li key={row.account_id}><span className="ov-icon"><Icon name="account"/></span><div><strong>{ACCOUNT[row.type]}</strong><small>{row.number}{row.status==='dormant'?' · Dormant':''}</small></div><b>{money(row.balance,row.currency)}</b></li>)}</ul>
            :<p className="ov-empty">You have no open accounts.</p>}</article>
        {portfolio&&<article className="ov-card" id="ov-investments" data-testid="overview-portfolio"><h2>Investments</h2>
          <p className="ov-empty">{portfolio.mandate==='advised'?'Advised portfolio':'Managed portfolio'} · {money(portfolio.total_value,portfolio.currency)} as of {formatDate(portfolio.as_of)}</p>
          <ul className="ov-rows">{portfolio.holdings.map(row=><li key={row.holding_id}><span className="ov-icon"><Icon name="chart"/></span>
            <div><strong>{row.description}</strong><small>{ASSET[row.asset_class]}</small></div><b>{money(row.market_value,row.currency)}</b></li>)}</ul>
          <button className="source-link" onClick={()=>onAsk('Can I borrow against my investments?')}>Borrowing against your investments</button></article>}
        {cards.length>0&&<article className="ov-card" id="ov-cards"><h2>Cards</h2><ul className="ov-rows ov-cards">{cards.map(row=><li key={row.card_id} className={managing===row.card_id?'managing':''}>
          <div className="ov-card-row"><span className="ov-icon"><Icon name="card"/></span>
          <div><strong>{row.name}</strong><small>•••• {row.last4}{row.status==='temporarily_blocked'?'':row.status!=='active'?` · ${row.status.replaceAll('_',' ')}`:''}{row.due_date?` · minimum ${money(row.minimum_payment,row.currency||undefined)} due ${formatDate(row.due_date)}`:''}</small>
            {row.status==='temporarily_blocked'&&<span className="ov-frozen" data-testid="card-frozen"><Icon name="lock"/>Frozen</span>}</div>
          <b>{money(row.outstanding,row.currency||undefined)}<small>of {money(row.limit,row.currency||undefined)} limit</small></b>
          <button className="ov-manage" data-testid="card-manage" aria-expanded={managing===row.card_id} onClick={()=>setManaging(managing===row.card_id?null:row.card_id)}>Manage<Icon name="chevron"/></button></div>
          {managing===row.card_id&&<CardControls card={row} onChanged={()=>setReload(n=>n+1)} onRaise={onRaise}/>}</li>)}</ul></article>}
        {loans.length>0&&<article className="ov-card" id="ov-loans"><h2>Loans</h2><ul className="ov-rows">{loans.map(row=><li key={row.loan_id}><span className="ov-icon"><Icon name="loan"/></span>
          <div><strong>{LOAN[row.type]}</strong><small>Next payment {money(row.next_amount,row.currency||undefined)}{row.next_payment?` on ${formatDate(row.next_payment)}`:''}</small></div><b>{money(row.outstanding,row.currency||undefined)}<small>outstanding</small></b></li>)}</ul></article>}
        <article className="ov-card" id="ov-transactions"><h2>{allTxns?'All activity':'Recent activity'}</h2>
          {allTxns&&<div className="ov-txn-filter" data-testid="overview-activity-filter"><label>From<input type="date" value={from} max={to||undefined} onChange={e=>setFrom(e.target.value)}/></label>
            <label>To<input type="date" value={to} min={from||undefined} onChange={e=>setTo(e.target.value)}/></label>
            {(from||to)&&<button type="button" className="link-button" onClick={()=>{setFrom('');setTo('');}}>Clear dates</button>}
            <span className="ov-small">{recent.length} transaction{recent.length===1?'':'s'}{sorted.length>=200?' · the latest 200 are shown':''}</span></div>}
          {recent.length?<ul className="ov-rows ov-txns">{recent.map(row=><li key={row.txn_id}><div><strong>{row.description}</strong><small>{formatDate(row.date)}{row.card_last4?` · Card •••• ${row.card_last4}`:''}{row.status!=='posted'?` · ${row.status}`:''}</small></div>
            <b className={row.amount<0?'out':'in'}>{row.amount<0?'−':'+'}{money(Math.abs(row.amount),row.currency)}</b>
            {onRaise&&disputable.has(row.txn_id)&&<button className="ov-txn-dispute" data-testid="txn-dispute" title="Dispute a charge you recognise but think is wrong"
              onClick={()=>onRaise({category:'card_dispute',details:{card:row.card_last4,transaction:row.txn_id},summary:`Dispute the ${row.description} charge on ${formatDate(row.date)}.`})}>Dispute</button>}</li>)}</ul>
            :<p className="ov-empty">{allTxns?'No transactions in these dates.':'No recent transactions.'}</p>}
          <div className="ov-links"><button className="source-link" data-testid="overview-all-activity" aria-expanded={allTxns} onClick={()=>setAllTxns(o=>!o)}>{allTxns?'Show the latest 5':'See all and filter by date'}</button>
            <button className="source-link" onClick={()=>onAsk('Show my recent transactions')}>Ask about a transaction</button></div></article>
        <Statements records={records} onRequest={onRequestStatement}/>
      </div>
      <aside className="ov-side">
        <article className="ov-card ov-tile" data-testid="overview-help"><span className="eyebrow">Your relationship manager</span>
          {rm?<><h2>{displayName(rm.name)}</h2><p className="ov-small">{rm.team} · usually replies within one business day</p></>:<p className="ov-small">No relationship manager is recorded right now.</p>}
          <div className="ov-tile-actions">{onSupport&&<button className="bank-chat-logout" onClick={onSupport}>Message {rm?rmFirst:'the bank'}</button>}
            <button className="bank-chat-logout" onClick={()=>onNavigate('meetings')}>Request a meeting</button></div></article>
        <article className={`ov-card ov-tile ${reviewStep?.needsAttention?'attention':''}`} data-testid="overview-compliance"><span className="eyebrow">Periodic review</span>
          {open?<><h2>{missing.length?`${missing.length} document${missing.length===1?'':'s'} to send`:reviewStep.label}</h2><p className="ov-small">{review.due_date?`Due ${formatDate(review.due_date)}`:review.title}</p>
            <button className={reviewStep.needsAttention?'primary-button':'bank-chat-logout'} onClick={()=>onNavigate('compliance')}>{reviewStep.needsAttention?'Continue review':'View review'}<Icon name="arrow"/></button></>
            :<><p className="ov-ok"><Icon name="check"/>Nothing needed from you</p><p className="ov-small">We’ll tell you in your Inbox when your next review starts.</p></>}</article>
        <article className="ov-card ov-tile" data-testid="overview-details"><span className="eyebrow">Your details</span>
          <p>{displayName(records.profile.data.name)} · {String(records.profile.data.employment_status).replace('_','-')} · {records.profile.data.preferred_language==='en'?'English':records.profile.data.preferred_language}</p>
          {onRecords&&<button className="bank-chat-logout" data-testid="open-records" onClick={onRecords}>See all your records<Icon name="arrow"/></button>}</article>
        <article className="ov-card ov-tile"><span className="eyebrow">Need help?</span><p className="ov-small">Ask the assistant first. If it can’t help, it passes your question to {rmFirst} with a reference.</p>
          <button className="bank-chat-logout" onClick={()=>onNavigate('chat')}>Open the assistant<Icon name="arrow"/></button></article>
      </aside>
    </div>
  </section>;
}
