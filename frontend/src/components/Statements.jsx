import {useEffect,useMemo,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import Icon from './Icon';
import {buildStatement,lastFullMonths,monthLabel,statementSources} from '../lib/statements';
import {displayName,formatDate,formatDateTime,formatMoney as money} from '../lib/format';

const ACCOUNT={savings:'Savings account',current:'Current account',multi_currency:'Multi-currency account'};
const sourceName=({kind,source})=>kind==='card'?`${source.name} •••• ${source.last4}`:`${ACCOUNT[source.type]||'Account'} ${source.number}`;

// CR15: monthly statements for the last 3 full months, built from the client's own records and printed or saved as a PDF
// with the browser. Older or certified copies are a Statement or document copy request.
export default function Statements({records,onRequest}) {
  const sources=useMemo(()=>statementSources(records.accounts.data,records.cards.data),[records]);
  const months=useMemo(()=>lastFullMonths(),[]);
  const [key,setKey]=useState(sources[0]?.key||''),[month,setMonth]=useState(months[0]),[open,setOpen]=useState(null);
  const chosen=sources.find(source=>source.key===key)||sources[0];
  return <article className="ov-card" id="statements" data-testid="overview-statements"><h2>Statements</h2>
    <p className="ov-small">Statements for the last 3 full months. To keep a copy, open one and choose Print or save as PDF.</p>
    {chosen?<div className="st-pick">
      <label>Account or card<select data-testid="statement-source" value={chosen.key} onChange={event=>setKey(event.target.value)}>
        {sources.map(source=><option key={source.key} value={source.key}>{sourceName(source)}</option>)}</select></label>
      <label>Month<select data-testid="statement-month" value={month} onChange={event=>setMonth(event.target.value)}>
        {months.map(value=><option key={value} value={value}>{monthLabel(value)}</option>)}</select></label>
      <button className="primary-button" data-testid="statement-view" onClick={()=>setOpen({statement:buildStatement(chosen,records.transactions.data,month),source:chosen})}>View statement</button>
    </div>:<p className="ov-empty">You have no accounts or cards with a statement.</p>}
    <p className="ov-small st-older">Need an older month, a certified copy or a paper statement?{' '}
      <button className="source-link" data-testid="statement-request" onClick={onRequest}>Request a statement copy</button></p>
    {open&&<StatementSheet statement={open.statement} source={open.source} name={displayName(records.profile.data.name)} asOf={records.transactions.as_of} onClose={()=>setOpen(null)}/>}
  </article>;
}

function StatementSheet({statement,source,name,asOf,onClose}) {
  const close=useRef(null);
  useEffect(()=>{close.current?.focus();const key=event=>{if(event.key==='Escape')onClose();};window.addEventListener('keydown',key);return ()=>window.removeEventListener('keydown',key);},[onClose]);
  const card=statement.kind==='card',cur=statement.currency;
  // Rendered on <body> so printing can hide the rest of the app and place the sheet at the top of the page.
  return createPortal(<div className="st-overlay" role="dialog" aria-modal="true" aria-label={`Statement for ${monthLabel(statement.month)}`}>
    <div className="st-sheet statement-print" data-testid="statement-sheet">
      <div className="st-tools">
        <button className="primary-button" data-testid="statement-print" onClick={()=>window.print()}><Icon name="document"/>Print or save as PDF</button>
        <button className="bank-chat-logout" ref={close} onClick={onClose}>Close</button>
      </div>
      <header className="st-head"><div><strong className="st-bank">DemoBank</strong><span>Private Client Banking</span></div>
        <div><h2>{card?'Card statement':'Account statement'}</h2><span>{formatDate(statement.first)} – {formatDate(statement.last)}</span></div></header>
      <dl className="st-meta"><div><dt>Client</dt><dd>{name}</dd></div><div><dt>{card?'Card':'Account'}</dt><dd>{sourceName(source)}</dd></div>
        <div><dt>Currency</dt><dd>{cur}</dd></div>{card&&<div><dt>Credit limit</dt><dd>{money(source.source.limit)}</dd></div>}</dl>
      <dl className="st-sum">
        <div><dt>{card?'Owed at start':'Opening balance'}</dt><dd>{money(statement.opening,cur)}</dd></div>
        <div><dt>{card?'Payments and refunds':'Money in'}</dt><dd>{money(statement.money_in,cur)}</dd></div>
        <div><dt>{card?'Purchases':'Money out'}</dt><dd>{money(statement.money_out,cur)}</dd></div>
        <div><dt>{card?'Owed at end':'Closing balance'}</dt><dd data-testid="statement-closing">{money(statement.closing,cur)}</dd></div></dl>
      <table className="st-table"><thead><tr><th>Date</th><th>Description</th><th className="num">{card?'Charges':'Money out'}</th><th className="num">{card?'Credits':'Money in'}</th><th className="num">{card?'Owed':'Balance'}</th></tr></thead>
        <tbody><tr className="st-carry"><td>{formatDate(statement.first)}</td><td>{card?'Balance owed brought forward':'Balance brought forward'}</td><td/><td/><td className="num">{money(statement.opening,cur)}</td></tr>
          {statement.lines.map(line=><tr key={line.txn_id}><td>{formatDate(line.date)}</td><td>{line.description}</td>
            <td className="num">{line.amount<0?money(-line.amount,cur):''}</td><td className="num">{line.amount>0?money(line.amount,cur):''}</td><td className="num">{money(line.balance,cur)}</td></tr>)}
          {!statement.lines.length&&<tr><td colSpan="5" className="st-none">No transactions this month.</td></tr>}</tbody></table>
      {statement.pending.length>0&&<p className="st-note">Not included: {statement.pending.length} pending {statement.pending.length===1?'payment':'payments'} ({statement.pending.map(row=>`${row.description}, ${money(Math.abs(row.amount),row.currency)}`).join('; ')}). Pending payments appear on the statement for the month they complete.</p>}
      <footer className="st-foot">Prepared {formatDateTime(asOf)} from your DemoBank records. This is a synthetic coursework statement, not a real bank document. For a certified copy, raise a Statement or document copy request under My requests.</footer>
    </div></div>,document.body);
}
