import {useEffect,useRef,useState} from 'react';
import Icon from './Icon';
import SharingCategory from './SharingCategory';
import {supabase} from '../lib/supabase';
import {confirmAction,createActionCase,proposeAction} from '../lib/actions';
import {DELETE_ALL,DELETE_ALL_NOTE,accessGroups,alwaysLine,alwaysShared,askLine,loadAccessLog,loadAsks,loadSharing,openAsks,rowLine,rowState} from '../lib/consentSharing';
import {loadTalkingPoints} from '../lib/talkingPoints';
import {displayName,firstName,formatDate,formatDateTime,initials} from '../lib/format';

// Data sharing (redesign 8 Nov 2026): the top level answers one question, what her relationship manager can see, with
// one row per category (a status and one line); each row opens its detail one tap down, where she switches it, changes
// it and, for chat topics, sees each topic in plain words with Delete. Her words are never shown (0142). Every change is
// one ledger action she confirms here; the receipt is in Actions.
const ICONS={transaction_insights:'card',chat_topics:'chat',meeting_recording:'mic'};
const cap=text=>text[0].toUpperCase()+text.slice(1);

export default function SharingPage({onReviewActions,initial=null}) {
  const [sharing,setSharing]=useState(null),[asks,setAsks]=useState([]),[log,setLog]=useState(null),[topics,setTopics]=useState(null);
  const [busy,setBusy]=useState(true),[saving,setSaving]=useState(false),[error,setError]=useState(''),[topicsError,setTopicsError]=useState(''),[done,setDone]=useState('');
  const [view,setView]=useState(initial||'list'),[deleting,setDeleting]=useState(false),[all,setAll]=useState(false);
  const active=useRef(null),top=useRef(null);
  useEffect(()=>{refresh();return ()=>active.current?.abort();},[]);
  useEffect(()=>{top.current?.scrollIntoView?.({block:'start'});},[view]);
  async function token() {
    const {data,error:sessionError}=await supabase.auth.getSession();
    if(sessionError)throw sessionError;
    if(!data.session)throw new Error('Please sign in again to review data sharing.');
    return data.session.access_token;
  }
  async function refresh() {
    const controller=new AbortController();active.current=controller;setBusy(true);setError('');
    try {
      const key=await token();
      const [found,asked,seen,mine]=await Promise.all([loadSharing(key,controller.signal),loadAsks(key,controller.signal),loadAccessLog(key,controller.signal),
        loadTalkingPoints(key,controller.signal).then(value=>({value}),failure=>({failure}))]);
      if(controller.signal.aborted)return;
      if(mine.failure?.name==='AbortError')return;
      setSharing(found);setAsks(asked);setLog(seen);setTopics(mine.value||null);setTopicsError(mine.failure?.message||'');
    } catch(failure) {if(failure.name!=='AbortError'&&!controller.signal.aborted){setError(failure.message);setSharing(null);}}
    finally {if(!controller.signal.aborted)setBusy(false);}
  }
  // Propose, then confirm when it waits for her (she reviewed it here); a receipt that did not succeed means something changed first.
  async function act(type,target,message,task='data-sharing') {
    const controller=new AbortController();active.current=controller;setSaving(true);setError('');setDone('');
    try {
      const key=await token();
      const scope=await createActionCase({session_id:crypto.randomUUID(),case_id:crypto.randomUUID()},key,controller.signal);
      let result=await proposeAction({type,target,command_id:crypto.randomUUID(),task_id:task,case_id:scope.case_id},key,controller.signal);
      if(result.command.state==='proposed')result=await confirmAction(result.command.command_id,key,controller.signal);
      if(controller.signal.aborted)return false;
      if(result.receipt?.outcome!=='succeeded')throw new Error('This change did not go through because something changed first. Refresh, then try again.');
      setDone(message);setDeleting(false);await refresh();return true;
    } catch(failure) {if(failure.name!=='AbortError'&&!controller.signal.aborted)setError(failure.message);return false;}
    finally {if(!controller.signal.aborted)setSaving(false);}
  }
  function open(next) {setView(next);setDone('');setError('');}
  const rm=sharing?.rm?firstName(sharing.rm.name):topics?.rm_first_name||'your relationship manager',Rm=cap(rm),off=busy||saving;
  const cards=sharing?sharing.categories.filter(c=>c.gated):[],rows=log?.rows||[],seen=accessGroups(rows,Rm),card=cards.find(c=>c.code===view);
  const status=<>
    {busy&&<p className="loading" role="status">{sharing?'Updating…':'Loading your data sharing…'}</p>}
    {error&&<p role="alert" className="sharing-alert" data-testid="sharing-error">{error}</p>}
    {done&&<p role="status" className="sharing-done" data-testid="sharing-done">{done} <button type="button" className="sharing-link" onClick={onReviewActions}>See the receipt in Actions</button></p>}
  </>;
  const back=<button type="button" className="sharing-back" data-testid="sharing-back" onClick={()=>open('list')}><Icon name="chevron"/>Data sharing</button>;
  if(view!=='list') return <section className="app-page-body sharing-page page-enter" aria-label="Data sharing" data-testid="sharing-page" data-view={view} ref={top}>
    {back}
    {!sharing&&status}
    {card&&<SharingCategory card={card} sharing={sharing} rm={rm} asks={openAsks(asks,card.code)} topics={topics} topicsError={topicsError} busy={busy} saving={saving} act={act} status={status}/>}
    {sharing&&view==='always'&&<><h1>Always shared with {rm}</h1>{status}
      <p className="sharing-lead">{Rm} needs these to look after you, so they can't be switched off.</p>
      <ul className="sharing-list" data-testid="consent-always-shared">{alwaysShared(sharing).map(c=><li key={c.code}><strong>{c.label}</strong>{c.reason&&<small>{c.reason}</small>}</li>)}</ul></>}
    {sharing&&view==='access'&&<><h1>Who looked and when</h1>{status}
      <p className="sharing-lead">Every time a relationship manager opens what you share, newest first.</p>
      {seen.length===0?<p className="page-empty" data-testid="consent-access-empty">No relationship manager has looked at what you share yet.</p>:
      <ul className="sharing-list" data-testid="consent-access-log">{(all?seen:seen.slice(0,10)).map(group=><li key={group.key}>
        <span>{group.line}</span><small><time dateTime={group.at}>{formatDateTime(group.at)}</time>{group.count>1&&` · ${group.count} times`}</small></li>)}</ul>}
      {seen.length>10&&!all&&<button type="button" className="bank-chat-logout sharing-more-rows" onClick={()=>setAll(true)}>Show all {seen.length}</button>}</>}
  </section>;
  return <section className="app-page-body sharing-page page-enter" aria-label="Data sharing" data-testid="sharing-page" data-view="list" ref={top}>
    <nav className="breadcrumb" aria-label="Breadcrumb"><span>Account</span><span aria-hidden="true">/</span><span aria-current="page">Data sharing</span></nav>
    <header className="sharing-head">
      {sharing?.rm&&<span className="initials" aria-hidden="true">{initials(sharing.rm.name)}</span>}
      <div><h1>What {rm} can see</h1>
        <p>{sharing?.rm?<><span data-testid="sharing-recipient">{displayName(sharing.rm.name)}</span> · your relationship manager. You can change this at any time.</>
          :sharing?'No relationship manager is assigned to you, so nothing below is shared.':'Your relationship manager sees only what you choose, plus what they need to look after you.'}</p></div>
    </header>
    {status}
    {sharing&&openAsks(asks).map(ask=><div key={ask.ask_id} className="sharing-ask" data-testid="consent-ask">
      <p>{askLine(ask,formatDateTime)} If you do nothing, this closes on {formatDate(ask.lapses_at)}.</p>
      <div className="panel-actions">
        <button type="button" className="primary-button" data-testid="consent-ask-share" disabled={off} onClick={()=>open(ask.category)}>Choose what to share</button>
        <button type="button" className="bank-chat-logout" data-testid="consent-ask-decline" disabled={off}
          onClick={()=>act('consent.decline_ask',{ask_id:ask.ask_id},`${Rm} has been told you're not sharing this now.`)}>Not now</button>
      </div></div>)}
    {sharing&&<ul className="sharing-rows" data-testid="sharing-rows">
      {cards.map(c=>{const state=rowState(c);return <li key={c.code}><button type="button" className="sharing-row" data-testid={`sharing-row-${c.code}`} data-on={state.on} onClick={()=>open(c.code)}>
        <Icon name={ICONS[c.code]||'shield'}/><span className="sharing-row-text"><strong>{c.label}</strong><small>{rowLine(c,sharing,Rm,formatDate,topics)}</small></span>
        <span className={`sharing-pill ${state.tone}`}>{state.pill}</span><span className="sharing-go" aria-hidden="true"><Icon name="chevron"/></span></button></li>;})}
      <li><button type="button" className="sharing-row" data-testid="sharing-row-always" onClick={()=>open('always')}>
        <Icon name="lock"/><span className="sharing-row-text"><strong>Always shared</strong><small>{alwaysLine(sharing)}</small></span>
        <span className="sharing-go" aria-hidden="true"><Icon name="chevron"/></span></button></li>
      <li><button type="button" className="sharing-row" data-testid="sharing-row-access" onClick={()=>open('access')}>
        <Icon name="clock"/><span className="sharing-row-text"><strong>Who looked and when</strong>
          <small>{rows.length?`Last: ${formatDateTime(rows[0].at)}`:'No one has looked yet'}</small></span>
        <span className="sharing-go" aria-hidden="true"><Icon name="chevron"/></span></button></li>
    </ul>}
    {sharing&&<div className="sharing-foot">
      {!deleting?<button type="button" className="sharing-link danger" data-testid="consent-delete-all" disabled={off} onClick={()=>{setDeleting(true);setDone('');}}>Delete all my data</button>:
      <div className="sharing-confirm" data-testid="consent-delete-all-review"><p>{DELETE_ALL_NOTE}</p>
        <div className="panel-actions"><button type="button" className="primary-button" data-testid="consent-delete-all-confirm" disabled={off}
          onClick={()=>act('ticket.create',DELETE_ALL,'Your request is with our data protection team. You can follow it in My requests.')}>Send the request</button>
          <button type="button" className="bank-chat-logout" disabled={saving} onClick={()=>setDeleting(false)}>Cancel</button></div></div>}
    </div>}
  </section>;
}
