import {useEffect,useRef,useState} from 'react';
import Icon from './Icon';
import ConsentControls from './ConsentControls';
import ConsentScope from './ConsentScope';
import AgendaDrafts from './AgendaDrafts';
import {supabase} from '../lib/supabase';
import {loadConsentRecords,loadSharingStatus} from '../lib/consent';
import {consentCategoryText} from '../lib/consentData';
import {displayName,firstName,formatDate,formatDateTime} from '../lib/format';

export function initials(name) {return name.split(/\s+/).filter(Boolean).slice(0,2).map(part=>part[0].toUpperCase()).join('');}

export default function SharingPage({embedded=false,onReviewActions,onRelationship}) {
  const [status,setStatus]=useState(null);
  const [registry,setRegistry]=useState(null);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [agendas,setAgendas]=useState(false);
  const active=useRef(null);
  const manage=useRef(null);
  useEffect(()=>{refresh();return ()=>active.current?.abort();},[]);
  async function refresh() {
    const controller=new AbortController();active.current=controller;setBusy(true);setError('');setStatus(null);setRegistry(null);
    try {
      const {data,error:sessionError}=await supabase.auth.getSession();
      if(sessionError)throw sessionError;
      if(!data.session)throw new Error('Please sign in again to review data sharing.');
      const [result,records]=await Promise.all([loadSharingStatus(data.session.access_token,controller.signal),loadConsentRecords(data.session.access_token,controller.signal)]);
      if(!controller.signal.aborted){setStatus(result);setRegistry(records);}
    } catch(failure) {if(failure.name!=='AbortError'&&!controller.signal.aborted){setError(failure.message);setStatus(null);setRegistry(null);}}
    finally {if(!controller.signal.aborted)setBusy(false);}
  }
  const recipient=status?.recipient,granted=status?.consent?.granted;
  const history=registry?[...registry.grants].sort((a,b)=>Date.parse(b.granted_at)-Date.parse(a.granted_at)):[];
  return <section className={`${embedded?'sharing-embedded':'app-page-body'} sharing-page`} aria-label="Data sharing" data-testid="sharing-page">
    {!embedded&&<nav className="breadcrumb" aria-label="Breadcrumb"><span>Account</span><span aria-hidden="true">/</span><span aria-current="page">Data sharing</span></nav>}
    <div className="page-header"><div>{embedded?<><span className="eyebrow">Data sharing</span><h2>Data sharing &amp; permissions</h2></>:<h1>Data sharing</h1>}<p>Control who can see your information, and for how long. Checking this status does not share data or change your consent.</p></div></div>
    {busy&&<p className="loading" role="status">Checking data-sharing consent…</p>}
    {error&&<p role="alert" data-testid="sharing-error">{error}</p>}
    <section className="page-section"><h2 className="section-label">Who has access</h2>
      {recipient&&<div className="access-card" data-testid="sharing-details">
        <div className="access-person"><span className="initials" aria-hidden="true">{initials(recipient.name)}</span><div>
          <h3><span data-testid="sharing-recipient">{displayName(recipient.name)}</span><span className={`status-badge ${granted?'active':''}`}><span className="dot"/>{granted?'Active':'No consent'}</span></h3>
          <p>Relationship manager · Data covered: Basic profile</p>
          <p role="status" data-testid="sharing-status">{granted?'You have active consent for this profile sharing.':'There is no active consent for this profile sharing.'}</p>
          {status.consent.expires_at&&<p>Consent expires: <time dateTime={status.consent.expires_at}>{formatDateTime(status.consent.expires_at)}</time></p>}
        </div></div>
        <div className="panel-actions"><button className="bank-chat-logout" onClick={()=>manage.current?.scrollIntoView({behavior:'smooth',block:'start'})}>Manage sharing</button>
          <button className="bank-chat-logout" data-testid="open-agenda-drafts" aria-expanded={agendas} onClick={()=>setAgendas(!agendas)}>Prepare for meeting</button></div>
      </div>}
      {status&&!recipient&&<p className="page-empty" role="status" data-testid="sharing-no-manager">No relationship manager is currently assigned to your demo profile.</p>}
      {!recipient&&<button className="bank-chat-logout" data-testid="open-agenda-drafts" aria-expanded={agendas} onClick={()=>setAgendas(!agendas)}>Prepare meeting agenda</button>}
    </section>
    {agendas&&<AgendaDrafts recipient={recipient} consentRegistry={registry} consentBusy={busy} onReviewActions={onReviewActions}/>}
    {registry&&<section className="page-section"><h2 className="section-label">What {recipient?firstName(recipient.name):'your manager'} can see</h2><ConsentScope registry={registry} recipient={recipient}/></section>}
    {registry&&<section className="page-section"><h2 className="section-label">Your consent history</h2>
      {history.length?<div className="table-frame"><table className="history-table"><thead><tr><th>Granted</th><th>Data and recipient</th><th>Status</th></tr></thead>
        <tbody>{history.map(item=>{const state=item.revoked_at?'revoked':item.active?'active':'inactive';return <tr key={item.grant_id}><td><time dateTime={item.granted_at}>{formatDate(item.granted_at)}</time></td><td>{consentCategoryText(item.categories)}<small>{item.recipient_name?displayName(item.recipient_name):item.recipient}</small></td><td><span className={`status-badge ${state}`}>{state[0].toUpperCase()+state.slice(1)}</span></td></tr>;})}</tbody></table></div>
        :<p className="page-empty">No grants are recorded yet.</p>}
      <p className="page-note">You can change or withdraw your consent at any time.</p></section>}
    <section className="page-section consent-card" ref={manage}><ConsentControls registry={registry} recipient={recipient} busy={busy} onRefresh={refresh} onReviewActions={onReviewActions}/></section>
    <button className="bank-chat-logout" data-testid="open-relationship-details" onClick={onRelationship}><Icon name="calendar"/>View requests and appointment times</button>
  </section>;
}
