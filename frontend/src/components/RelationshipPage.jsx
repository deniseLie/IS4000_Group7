import {useEffect,useRef,useState} from 'react';
import Icon from './Icon';
import RelationshipDetails from './RelationshipDetails';
import RelationshipWorkspace from './RelationshipWorkspace';
import SharingPage from './SharingPage';
import {initials} from './SharingPage';
import {supabase} from '../lib/supabase';
import {loadSharingStatus} from '../lib/consent';
import {displayName} from '../lib/format';

// Workflow 3 task page. The chat hands off here; "Guided steps" follows the workflow, "All requests & appointments" keeps the full manager view and "Data sharing & permissions" embeds consent.
export default function RelationshipPage({intro,introError,focus,onFocus,mode,onMode,onBack,onAsk,onSource,onReviewActions,onSharing,onSupport}) {
  const [status,setStatus]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const active=useRef(null);
  useEffect(()=>{
    const controller=new AbortController();active.current=controller;setBusy(true);
    (async()=>{
      try {
        const {data,error:sessionError}=await supabase.auth.getSession();
        if(sessionError)throw sessionError;
        if(!data.session)throw new Error('Please sign in again to view relationship support.');
        const sharing=await loadSharingStatus(data.session.access_token,controller.signal);
        if(!controller.signal.aborted)setStatus(sharing);
      } catch(failure) {if(failure.name!=='AbortError'&&!controller.signal.aborted)setError(failure.message);}
      finally {if(!controller.signal.aborted)setBusy(false);}
    })();
    return ()=>controller.abort();
  },[]);
  const current=intro?.current,recipient=status?.recipient;
  const view=current?mode:mode==='sharing'?'sharing':'all';
  return <section className="app-page-body relationship-page rm-page" aria-label="Relationship and advisory" data-testid="relationship-page">
    <button className="rm-back" data-testid="rm-back-to-chat" onClick={onBack}><Icon name="chevron"/>Back to chat</button>
    <header className="rm-page-head">
      <div><h1>Your relationship manager</h1><p>Send requests, share a handover note and book a meeting. Each step is confirmed by you first.</p></div>
      {current&&<div className="rm-page-rm"><span className="initials" aria-hidden="true">{initials(current.name)}</span>
        <div><strong>{displayName(current.name)}</strong><small>{current.role} · {current.contact.phone||'phone not recorded'}</small></div></div>}
    </header>
    {(error||introError)&&<p role="alert">{error||introError}</p>}
    {!intro&&!introError&&busy&&<><p className="loading" role="status">Loading your relationship manager…</p><div className="loading-list" aria-hidden="true"><span className="skeleton-block"/><span className="skeleton-card large"/></div></>}
    {!current&&intro&&<div className="rm-fallback"><p>{intro.notice[0]?.body||'No current relationship manager is recorded.'}</p><button className="bank-chat-logout" onClick={onSupport}><Icon name="support"/>Talk to a person</button></div>}
    {(current||intro)&&<div className="rm-mode" role="tablist" aria-label="Relationship view">
      {[...(current?[['guided','Guided steps']]:[]),['all','All requests & appointments'],['sharing','Data sharing & permissions']].map(([value,label])=><button key={value} role="tab" data-testid={`rm-mode-${value}`} aria-selected={view===value} className={view===value?'active':''} onClick={()=>onMode(value)}>{label}</button>)}
    </div>}
    {view==='guided'&&<RelationshipWorkspace embedded intro={intro} focus={focus} onFocus={onFocus} onSupport={onSupport} onAsk={onAsk} onSource={onSource}/>}
    {view==='all'&&<div className="rm-all">
      {busy&&<p className="loading" role="status">Checking relationship records…</p>}
      <div className="scheduler-card"><RelationshipDetails recipient={recipient} recipientBusy={busy} onReviewActions={onReviewActions}/></div>
      <p className="rm-small">Consent and what your relationship manager can see are managed in <button className="source-link" onClick={()=>onMode('sharing')}>Data sharing &amp; permissions</button>.</p>
    </div>}
    {view==='sharing'&&<SharingPage embedded onReviewActions={onReviewActions} onRelationship={()=>onMode('all')}/>}
  </section>;
}
