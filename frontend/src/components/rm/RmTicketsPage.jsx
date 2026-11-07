import {useEffect,useRef,useState} from 'react';
import Icon from '../Icon';
import {STATUS_WORDS,duplicateNote,newId,possibleFraud,receivedText,rmGet,rmPost,ticketIssues,ticketParts,ticketRows,ticketTitle,ticketsByOwner} from '../../lib/rm';

const SHOWN=8;
const FILTERS=[['all','All'],['urgent','Urgent'],['late','Past promised date'],['new','New today']];
const match=(row,filter)=>filter==='all'||(filter==='urgent'&&row.lead.urgency==='urgent')||(filter==='late'&&row.overdue)||(filter==='new'&&(row.lead.age_hours??99)<24);
const tone=row=>row.lead.urgency==='urgent'?'urgent':row.overdue?'late':'open';

// RM7/RM8 (redesign): one row per request. Requests with the same client, type and words are ONE row (the oldest
// ticket leads: you reply there once). Each row has one plain title, one grey line and one button; the selected row
// opens on the right with her words, cards on record, what she has been told and a possible-fraud warning.
// "With bank teams" is read-only: those teams decide and update the client themselves; restricted detail is never shown.
export default function RmTicketsPage({token,onAsk}) {
  const [data,setData]=useState(null),[error,setError]=useState(''),[tab,setTab]=useState('mine'),[filter,setFilter]=useState('all'),[pick,setPick]=useState(null),[all,setAll]=useState(false);
  const active=useRef(null);
  async function load() {
    active.current?.abort();const controller=new AbortController();active.current=controller;setError('');
    try {const r=await rmGet('/tickets',await token(),controller.signal);if(!controller.signal.aborted)setData(r.data);}
    catch(failure) {if(failure.name!=='AbortError')setError(failure.message);}
  }
  useEffect(()=>{load();return ()=>active.current?.abort();},[]);  // eslint-disable-line react-hooks/exhaustive-deps
  const groups=ticketsByOwner(data);
  const mine=ticketRows(groups.mine),teams=ticketRows(groups.specialist);
  const rows=(tab==='mine'?mine:teams).filter(r=>match(r,filter));
  const shown=all?rows:rows.slice(0,SHOWN);
  const selected=rows.find(r=>r.key===pick)||rows[0]||null;
  const late=mine.filter(r=>r.overdue).length,count=k=>mine.filter(r=>match(r,k)).length;
  const draft=t=>onAsk(`Draft a reply to ${t.client} about ${t.reference}`,{intent:'draft_message',context:{client_id:t.customer_id,ticket_ref:t.reference}});
  return <div className="app-page-body rmw-page rm-inbox" data-testid="rm-tickets">
    <header className="page-header"><div><span className="eyebrow">Relationship manager</span><h1>Ticket inbox</h1>
      <p>{!data?'Reading your requests…':mine.length?`${mine.length} request${mine.length===1?'':'s'} waiting for you${late?` · ${late} past the promised date`:''}. Most urgent at the top.`:'Nothing waiting for you.'}</p></div>
      <div className="page-header-actions"><button className="bank-chat-logout" onClick={()=>onAsk('What needs my attention today?',{intent:'ticket_inbox'})}><Icon name="spark"/>Triage with assistant</button>
        <button className="icon-button" aria-label="Refresh" title="Refresh" onClick={load}><Icon name="refresh"/></button></div></header>
    <div className="rm-inbox-bar">
      <nav className="page-tabs" role="tablist">
        <button role="tab" aria-selected={tab==='mine'} data-testid="rm-tab-mine" onClick={()=>{setTab('mine');setPick(null);}}>Waiting for you · {mine.length}</button>
        <button role="tab" aria-selected={tab==='specialist'} data-testid="rm-tab-specialist" onClick={()=>{setTab('specialist');setPick(null);}}>With bank teams · {teams.length}</button></nav>
      {tab==='mine'&&<div className="rm-chips" aria-label="Show">{FILTERS.map(([k,label])=><button key={k} type="button" className={`rm-chip ${filter===k?'on':''}`} aria-pressed={filter===k} onClick={()=>{setFilter(k);setPick(null);}}>
        {k==='urgent'&&<span className="rm-dot urgent" aria-hidden="true"/>}{k==='late'&&<span className="rm-dot late" aria-hidden="true"/>}{label}{k!=='all'&&` · ${count(k)}`}</button>)}</div>}
    </div>
    {tab==='specialist'&&<p className="rmw-note"><Icon name="info"/>Read-only. These teams decide the case and update the client themselves. If the client asks you, share the status shown here.</p>}
    {error&&<p role="alert" className="rm-error">{error}</p>}
    {!data&&!error&&<div className="loading-list" role="status" aria-label="Loading">{[0,1,2].map(k=><span key={k} className="skeleton-card"/>)}</div>}
    {data&&<div className="rm-inbox-grid">
      <section className="rmw-card rm-inbox-list" aria-label={tab==='mine'?'Requests waiting for you':'Requests with bank teams'}>
        {!rows.length&&<p className="rm-none">{filter==='all'?'Nothing here.':'Nothing matches this filter.'}</p>}
        {shown.map((r,i)=>{const t=r.lead,on=selected?.key===r.key,issues=tab==='mine'?ticketIssues(t):[];return <div key={r.key} className={`rm-inbox-row ${on?'on':''}`} data-testid="rm-ticket" data-reference={t.reference}>
          <button type="button" className="rm-inbox-pick" aria-pressed={on} onClick={()=>setPick(r.key)}>
            <span className={`rm-dot ${tone(r)}`} aria-label={t.urgency==='urgent'?'Urgent':r.overdue?'Past the promised date':'Waiting'}/>
            <span className="rm-now-text"><strong>{t.client}</strong> · {ticketTitle(t)}
              <small className="rmw-why">{[r.count>1&&`${r.count} requests like this`,r.overdue?`past the promised date (${t.target_sgt})`:`due ${t.target_sgt}`,
                tab==='mine'?STATUS_WORDS[t.status]:`${t.owner_team} · client sees: ${t.client_safe_status}`,(t.age_hours??99)<24&&'new today'].filter(Boolean).join(' · ')}</small>
              {issues.length>1&&<span className="rm-issues">{issues.map(x=><span key={x}>{x}</span>)}</span>}</span></button>
          {tab==='mine'&&<button type="button" className={i===0&&!all&&filter==='all'?'primary-button':'bank-chat-logout'} data-testid="rm-ticket-draft" onClick={()=>draft(t)}>Draft a reply</button>}
        </div>;})}
        {rows.length>SHOWN&&<p className="rm-inbox-more">Showing {shown.length} of {rows.length} · <button type="button" className="link-button" onClick={()=>setAll(o=>!o)}>{all?'Show fewer':'Show all'}</button></p>}
      </section>
      {selected&&<TicketPanel key={selected.key} row={selected} mine={tab==='mine'} token={token} onDraft={draft} onDone={load}/>}
    </div>}
  </div>;
}

// The selected request: her words, cards on record, what she has been told, and (for a merged row) closing the
// duplicates with a short note. The note is shown before it is sent; each close is its own approved, audited message.
function TicketPanel({row,mine,token,onDraft,onDone}) {
  const t=row.lead,{words,cards,lead}=ticketParts(t.summary);
  const [detail,setDetail]=useState(null),[close,setClose]=useState(row.same.map(x=>x.reference)),[busy,setBusy]=useState(false),[notice,setNotice]=useState(''),[error,setError]=useState('');
  const ids=useRef(new Map());
  useEffect(()=>{const c=new AbortController();(async()=>{try{setDetail((await rmGet(`/tickets/${t.reference}`,await token(),c.signal)).data);}catch(f){if(f.name!=='AbortError')setError(f.message);}})();return ()=>c.abort();},[t.reference]);  // eslint-disable-line react-hooks/exhaustive-deps
  const told=detail?[...detail.told_by_bank.map(e=>({at:e.at_sgt,who:'Bank',text:e.note})),...detail.told_by_assistant.map(e=>({at:e.at_sgt,who:'Chat assistant',text:e.reply})),
    ...detail.sent_by_rm.map(e=>({at:e.at_sgt,who:'You',text:e.text}))]:[];
  const replied=Boolean(detail?.sent_by_rm?.length);
  async function closeSame() {
    setBusy(true);setError('');setNotice('');
    try {
      const auth=await token();
      for(const reference of close){if(!ids.current.has(reference))ids.current.set(reference,newId());
        await rmPost('/messages',{message_id:ids.current.get(reference),customer_id:t.customer_id,reference,body:duplicateNote(t),status:'resolved'},auth);}
      setNotice(`${close.length} duplicate${close.length===1?'':'s'} closed with a note pointing to ${t.reference}.`);onDone();
    } catch(failure) {setError(failure.message);} finally {setBusy(false);}
  }
  return <aside className="rmw-card rm-inbox-panel" aria-label="Selected request" data-testid="rm-ticket-detail">
    <div className="rm-panel-head"><span className="eyebrow">Selected</span><span className={`ticket-status ${t.status}`}>{STATUS_WORDS[t.status]}</span></div>
    <h3>{t.client} · {ticketTitle(t)}</h3>
    <p className="rm-small"><code>{t.reference}</code> · {receivedText(t.age_hours)} · {t.overdue?'was due':'due'} {t.target_sgt}{!mine&&` · ${t.owner_team}`}</p>
    {mine&&possibleFraud(t)&&<p className="rm-fraud" role="note"><strong>Possible fraud.</strong> An unknown charge with a code she didn’t ask for or a locked app. Check her card is frozen before replying.</p>}
    {!mine&&t.restricted?<p className="rm-none"><Icon name="lock"/>Details are restricted to {t.owner_team}.</p>:<>
      <div className="rm-panel-part"><span className="eyebrow">{words?'She wrote':'Request'}</span><p>{words?`“${words}”`:lead||t.summary}</p></div>
      {cards&&<div className="rm-panel-part"><span className="eyebrow">Cards on record</span><p>{cards}</p></div>}</>}
    {!mine&&<div className="rm-panel-part"><span className="eyebrow">Client sees</span><p>{t.client_safe_status}</p></div>}
    <div className="rm-panel-part"><span className="eyebrow">What she has been told</span>
      {!detail&&!error?<p className="rm-small">Loading…</p>:told.length?told.slice(-2).map((e,i)=><p key={i} className="rm-told-line"><b>{e.who}{e.at?` · ${e.at}`:''}</b>{e.text}</p>):<p className="rm-small">Nothing yet.</p>}</div>
    {mine&&row.same.length>0&&<div className="rm-panel-part rm-same" data-testid="rm-same-request"><span className="eyebrow">Same request, also open</span>
      <p className="rm-small">{row.same.length===1?'This ticket has':'These tickets have'} the same words. Reply once on {t.reference}, then close {row.same.length===1?'it':'them'} here.</p>
      {row.same.map(x=><label key={x.reference} className="rm-same-item"><input type="checkbox" checked={close.includes(x.reference)} disabled={busy} onChange={e=>setClose(c=>e.target.checked?[...c,x.reference]:c.filter(r=>r!==x.reference))}/>
        <span>Also close <code>{x.reference}</code> as the same request</span></label>)}
      <p className="rm-note-preview"><span className="eyebrow">{String(t.client||'The client').split(' ')[0]} will see</span>{duplicateNote(t)}</p>
      <button type="button" className="bank-chat-logout" data-testid="rm-close-same" disabled={busy||!close.length||!replied} onClick={closeSame}>{busy?'Closing…':`Close ${close.length||''} duplicate${close.length===1?'':'s'}`}</button>
      {!replied&&<p className="rm-small">Send your reply on {t.reference} first; then you can close the duplicates.</p>}</div>}
    {notice&&<p className="rm-success" role="status"><Icon name="check"/>{notice}</p>}
    {error&&<p className="rm-error" role="alert">{error}</p>}
    {mine&&<div className="rm-panel-actions"><button type="button" className="primary-button" onClick={()=>onDraft(t)}><Icon name="chat"/>Draft a reply</button></div>}
  </aside>;
}
