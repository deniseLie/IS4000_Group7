import {useEffect,useRef,useState} from 'react';
import Icon from '../Icon';
import RmAnswer from '../rm/RmAnswer';
import {briefNotice,newId,rmGet,rmPost} from '../../lib/rm';

// Brief Writer (0118): the saved, versioned pre-meeting brief of one confirmed meeting. It is written by the copilot's
// meeting brief with the RM's own sign-in, and re-read against the client's consent every time it opens: a topic or
// insight she withdrew shows as "Removed by client on <date>". The RM approves the exact version she read; a refresh
// writes a new version that needs approving again. Nothing here is sent to the client.
export default function MeetingBrief({meetingId,token,client,onClose,onAsk}) {
  const [data,setData]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(true),[status,setStatus]=useState('');
  const approvals=useRef(new Map());
  async function load(regenerate=false) {
    setBusy(true);setError('');
    try {
      const auth=await token();
      let result=await rmGet(`/meetings/${meetingId}/brief`,auth);
      if(regenerate||!result.brief) {
        setStatus(result.brief?'Writing a new version of the briefing from the current records…':'Preparing the briefing from the records…');
        result=await rmPost(`/meetings/${meetingId}/brief`,{regenerate},auth);
      }
      setData(result);
    } catch(failure) {setError(failure.message);}
    finally {setBusy(false);setStatus('');}
  }
  useEffect(()=>{setData(null);load();},[meetingId]);  // eslint-disable-line react-hooks/exhaustive-deps
  async function approve() {
    const b=data.brief,key=`${b.brief_id}:${b.version}`;
    if(!approvals.current.has(key))approvals.current.set(key,newId());
    setBusy(true);setError('');
    try {await rmPost(`/meetings/${meetingId}/brief/approve`,{brief_id:b.brief_id,version:b.version,command_id:approvals.current.get(key)},await token());await load();}
    catch(failure) {setError(failure.message);setBusy(false);}
  }
  const b=data?.brief,m=data?.meeting;
  return <section className="rmw-card meeting-brief" data-testid="meeting-brief" aria-label="Meeting briefing">
    <div className="rmw-card-head"><div><span className="eyebrow">Pre-meeting briefing</span>
      <h2><Icon name="spark"/>{client?`${client} · `:''}{m?.when||'Meeting'}</h2>
      {m?.purpose&&<p className="rm-small">{m.purpose}</p>}</div>
      <div className="meeting-actions">
        {b&&<button className="bank-chat-logout" data-testid="meeting-brief-refresh" disabled={busy} onClick={()=>load(true)}><Icon name="refresh"/>New version</button>}
        {onClose&&<button className="bank-chat-logout" onClick={onClose}><Icon name="close"/>Close</button>}</div></div>
    {status&&<p className="loading" role="status">{status}</p>}
    {error&&<p role="alert" className="rm-error">{error}</p>}
    {error&&onAsk&&m&&<button className="bank-chat-logout" onClick={()=>onAsk(`Brief me for my meeting on ${m.when}`,{intent:'meeting_brief',context:{client_id:m.customer_id,appointment_id:m.meeting_id}})}>
      Ask the assistant instead</button>}
    {b&&<>
      <p className="rm-small" data-testid="meeting-brief-version">Version {b.version} · written {b.created} · {b.brief_status==='approved'?'approved by you':'not approved yet'}</p>
      {briefNotice(b).map(line=><p key={line} className="rmw-warn" role="status"><Icon name="alert"/>{line}</p>)}
      <RmAnswer reply={b} token={token} client={client}/>
      {b.brief_status!=='approved'&&<div className="meeting-actions"><button className="primary-button" data-testid="meeting-brief-approve" disabled={busy} onClick={approve}>
        <Icon name="check"/>Approve version {b.version}</button><span className="rm-small">Approving records that you reviewed this exact version.</span></div>}</>}
  </section>;
}
