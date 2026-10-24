import {useEffect,useRef,useState} from 'react';
import Icon from './Icon';
import Stub from './Stub';
import ConsentScope from './ConsentScope';
import RelationshipDetails from './RelationshipDetails';
import {initials} from './SharingPage';
import {supabase} from '../lib/supabase';
import {loadConsentRecords,loadSharingStatus} from '../lib/consent';
import {loadSections} from '../lib/customerData';

export default function RelationshipPage({onReviewActions,onSharing,onSupport}) {
  const [status,setStatus]=useState(null);
  const [registry,setRegistry]=useState(null);
  const [advisor,setAdvisor]=useState(null);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const active=useRef(null);
  const scheduler=useRef(null);
  useEffect(()=>{
    const controller=new AbortController();active.current=controller;setBusy(true);
    async function load() {
      try {
        const {data,error:sessionError}=await supabase.auth.getSession();
        if(sessionError)throw sessionError;
        if(!data.session)throw new Error('Please sign in again to view relationship support.');
        const token=data.session.access_token;
        const [sharing,records,sections]=await Promise.all([loadSharingStatus(token,controller.signal),loadConsentRecords(token,controller.signal),loadSections(token,['advisor'],controller.signal)]);
        if(!controller.signal.aborted){setStatus(sharing);setRegistry(records);setAdvisor(sections.advisor);}
      } catch(failure) {if(failure.name!=='AbortError'&&!controller.signal.aborted)setError(failure.message);}
      finally {if(!controller.signal.aborted)setBusy(false);}
    }
    load();return ()=>controller.abort();
  },[]);
  const current=advisor?.data?.current,previous=advisor?.data?.previous,recipient=status?.recipient;
  const first=current?.name.split(' ')[0]||'your manager';
  return <section className="app-page-body relationship-page" aria-label="Relationship and advisory" data-testid="relationship-page">
    <div className="page-banner">
      <div className="banner-top"><span className="live-pill"><span className="dot"/>Relationship & advisory · Synthetic records</span><Stub inline>Private client #—</Stub></div>
      <div className="page-header"><div><h1>Your relationship team</h1><p>See who looks after your account, send a request and hold a meeting time. Booking and cancellation still need your review in Actions.</p></div><Stub inline>Mandate type —</Stub></div>
    </div>
    {busy&&<p role="status">Checking relationship records…</p>}
    {error&&<p role="alert">{error}</p>}
    {advisor&&<section className="transition-card" aria-label="Advisor transition and handover status">
      <span className="transition-icon"><Icon name="people"/></span>
      <div><h2>Advisor transition & handover status <span className="status-badge">{previous?'Handover recorded':'No change recorded'}</span></h2>
        <p>{current?<>{previous&&<><strong>{previous.name}</strong> ended on {previous.ended_on}. </>}<strong>{current.name}</strong> has been your relationship manager since {current.started_on}.</>:'No current relationship manager is recorded.'}</p>
        <div className="panel-actions"><button className="primary-button" onClick={()=>scheduler.current?.scrollIntoView({behavior:'smooth',block:'start'})}><Icon name="calendar"/>View appointment times</button><button className="bank-chat-logout" onClick={onSupport}><Icon name="support"/>Talk to a person</button></div>
      </div>
    </section>}
    <div className="relationship-grid">
      <div className="relationship-column">
        <div className="column-label"><span>Assigned advisor profile</span>{current&&<span className="status-badge active"><span className="dot"/>Current</span>}</div>
        <article className="advisor-card">
          {current?<div className="advisor-head"><span className="initials large" aria-hidden="true">{initials(current.name)}</span><div><h2>{current.name}</h2><p>{current.role} · {current.team}</p><p>Assigned since {current.started_on}</p></div></div>
            :<p className="page-empty">{advisor?'No current relationship manager recorded.':'Loading advisor profile…'}</p>}
          {current&&<dl className="sharing-details record-table"><dt>Email</dt><dd>{current.contact.email||'Not recorded'}</dd><dt>Phone</dt><dd>{current.contact.phone||'Not recorded'}</dd></dl>}
          <Stub title="Quick metrics"><div className="metric-row"><div><small>Mandates managed</small><strong>—</strong></div><div><small>Continuity rating</small><strong>—</strong></div></div></Stub>
          <Stub title="Working hours & office desk"><p>Office hours and desk location appear here in the design. Suggested times on the right already follow the manager's recorded working hours.</p></Stub>
          <Stub title="Advisory bio & personal note"><p>A personal introduction from the advisor appears here in the design. No advisor note is recorded.</p></Stub>
          <div className="panel-actions"><button className="bank-chat-logout" onClick={onSharing}><Icon name="shield"/>Data sharing</button><button className="bank-chat-logout" onClick={()=>document.querySelector('[data-testid="request-intake"]')?.scrollIntoView({behavior:'smooth',block:'start'})}><Icon name="chat"/>Send a request</button></div>
        </article>
      </div>
      <div className="relationship-column" ref={scheduler}>
        <div className="column-label"><span>Consultation scheduler</span><span><Icon name="lock"/>Holds last 10 minutes</span></div>
        <div className="scheduler-card"><RelationshipDetails recipient={recipient} recipientBusy={busy} onReviewActions={onReviewActions}/></div>
      </div>
    </div>
    {registry&&<section className="page-section"><div className="page-header"><div><h2 className="section-title">Data sharing</h2><p>You control what {first} can see.</p></div>
      <div className="page-header-actions"><button className="bank-chat-logout" onClick={onSharing}>Manage data sharing</button><button className="bank-chat-logout" onClick={onSharing}>Prepare for meeting</button></div></div>
      <ConsentScope registry={registry} recipient={recipient}/></section>}
    <p className="page-note"><Icon name="info"/>Coursework demo: records are synthetic and no real advisor is contacted.</p>
  </section>;
}
