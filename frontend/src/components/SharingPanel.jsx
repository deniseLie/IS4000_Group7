import {useEffect,useState} from 'react';
import Panel from './Panel';
import RelationshipDetails from './RelationshipDetails';
import {supabase} from '../lib/supabase';
import {loadSharingStatus} from '../lib/consent';

export default function SharingPanel({onClose}) {
  const [status,setStatus]=useState(null);
  const [error,setError]=useState('');
  const [relationship,setRelationship]=useState(false);
  useEffect(()=>{
    const controller=new AbortController();
    async function load() {
      try {
        const {data,error:sessionError}=await supabase.auth.getSession();
        if(sessionError)throw sessionError;
        if(!data.session)throw new Error('Please sign in again to review data sharing.');
        const result=await loadSharingStatus(data.session.access_token,controller.signal);
        if(!controller.signal.aborted)setStatus(result);
      } catch(failure) {if(failure.name!=='AbortError'&&!controller.signal.aborted)setError(failure.message);}
    }
    load();
    return ()=>controller.abort();
  },[]);
  return <Panel label="Data sharing" title="Review data-sharing consent" closeLabel="Close data sharing" onClose={onClose}>
    <p>Review whether your basic synthetic profile may be shared with your assigned relationship manager. Checking this status does not share data or change your consent.</p>
    {!status&&!error&&<p role="status">Checking data-sharing consent…</p>}
    {error&&<p role="alert" data-testid="sharing-error">{error}</p>}
    {status?.recipient&&<div data-testid="sharing-details">
      <dl className="sharing-details"><dt>Relationship manager</dt><dd data-testid="sharing-recipient">{status.recipient.name}</dd><dt>Data covered</dt><dd>Basic profile</dd></dl>
      <p role="status" data-testid="sharing-status">{status.consent.granted?'You have active consent for this profile sharing.':'There is no active consent for this profile sharing.'}</p>
      {status.consent.expires_at&&<p>Consent expires: <time dateTime={status.consent.expires_at}>{new Date(status.consent.expires_at).toLocaleString()}</time></p>}
    </div>}
    {status&&!status.recipient&&<p role="status" data-testid="sharing-no-manager">No relationship manager is currently assigned to your demo profile.</p>}
    <p className="source-notice">This is a read-only view. No data has been shared.</p>
    <button className="bank-chat-logout" data-testid="open-relationship-details" aria-expanded={relationship} onClick={()=>setRelationship(!relationship)}>View requests and appointment times</button>
    {relationship&&<RelationshipDetails/>}
  </Panel>;
}
