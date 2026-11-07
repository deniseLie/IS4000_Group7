import {useEffect,useRef,useState} from 'react';
import {meetingRequest} from '../../lib/meetingLifecycle';
import {formatDateTime} from '../../lib/format';
import {topicLabel} from '../../lib/rm';

export default function MeetingProposal({token,onScheduled,bare=false}){
  const [who,setWho]=useState(null),[client,setClient]=useState(''),[slots,setSlots]=useState([]),[time,setTime]=useState(''),[purpose,setPurpose]=useState(''),[topic,setTopic]=useState('');
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');const ids=useRef(new Map());
  useEffect(()=>{const control=new AbortController();(async()=>{try{const auth=await token(),cap=await meetingRequest('/capabilities','GET',null,auth,control.signal);
    if(!cap.enabled)return;const data=await meetingRequest('/session','GET',null,auth,control.signal);if(!control.signal.aborted){setWho(data);setTopic(data.topics[0]||'');}}
    catch(failure){if(failure.name!=='AbortError')setError(failure.message);}})();return()=>control.abort();},[]);
  useEffect(()=>{if(!client){setSlots([]);setTime('');return;}const control=new AbortController();setBusy(true);setError('');
    (async()=>{try{const data=await meetingRequest(`/slots/${client}`,'GET',null,await token(),control.signal);if(!control.signal.aborted){setSlots(data.slots);setTime('');}}
      catch(failure){if(failure.name!=='AbortError')setError(failure.message);}finally{if(!control.signal.aborted)setBusy(false);}})();return()=>control.abort();},[client]);
  async function submit(event){event.preventDefault();setBusy(true);setError('');setNotice('');const key=JSON.stringify([client,time,purpose,topic]);if(!ids.current.has(key))ids.current.set(key,crypto.randomUUID());
    try{await meetingRequest('/proposals','POST',{customer_id:Number(client),starts_at:time,purpose,topics:[topic],command_id:ids.current.get(key)},await token(),undefined);
      setNotice('Time proposed. The client reviews and accepts it in Meetings.');setPurpose('');setTime('');onScheduled();}
    catch(failure){setError(failure.message);}finally{setBusy(false);}}
  if(!who)return error?<p className="rm-error" role="alert">{error}</p>:null;
  return <section className={bare?'meeting-proposal-bare':'rmw-card meeting-lifecycle'}>{!bare&&<h3>Propose a meeting to a client</h3>}<p className="rm-small">Choose an agreed agenda and free time. The client must accept the proposal before it is confirmed.</p>
    {error&&<p className="rm-error" role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}<form onSubmit={submit} data-testid="rm-meeting-proposal"><div className="rm-form-grid">
      <label className="form-field">Client<select data-testid="proposal-client" value={client} onChange={e=>setClient(e.target.value)} disabled={busy} required><option value="">Choose client</option>{who.clients.map(c=><option key={c.customer_id} value={c.customer_id}>{c.name}</option>)}</select></label>
      <label className="form-field">Topic<select value={topic} onChange={e=>setTopic(e.target.value)} disabled={busy} required>{who.topics.map(t=><option key={t} value={t}>{topicLabel(t)}</option>)}</select></label></div>
      <label className="form-field">Purpose<textarea data-testid="proposal-purpose" value={purpose} onChange={e=>setPurpose(e.target.value)} maxLength={200} disabled={busy} required/></label>
      <label className="form-field">Free time<select data-testid="proposal-time" value={time} onChange={e=>setTime(e.target.value)} required disabled={busy||!client}><option value="">Choose time</option>{slots.map(s=><option key={s.starts_at} value={s.starts_at}>{formatDateTime(s.starts_at)}</option>)}</select></label>
      <button className="primary-button" data-testid="proposal-send" disabled={busy||!time||!purpose.trim()}>Propose to client</button></form></section>;
}
