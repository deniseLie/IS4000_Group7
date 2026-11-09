import {useEffect,useState} from 'react';
import {meetingRequest} from '../../lib/meetingLifecycle';
import {formatDateTime} from '../../lib/format';

export default function MeetingReminders({token,onOpen}){
  const [rows,setRows]=useState([]),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  async function load(signal){
    const auth=await token();const capability=await meetingRequest('/capabilities','GET',null,auth,signal);
    if(!capability.enabled){setRows([]);return;}
    const reminders=await meetingRequest('/reminders','GET',null,auth,signal);
    if(!signal.aborted)setRows(reminders);
  }
  useEffect(()=>{const control=new AbortController();load(control.signal).catch(failure=>{if(failure.name!=='AbortError')setError(failure.message);});return()=>control.abort();},[]);
  async function acknowledge(id){
    const control=new AbortController();setBusy(true);setError('');
    try{await meetingRequest(`/reminders/${id}/acknowledge`,'POST',null,await token(),control.signal);await load(control.signal);}
    catch(failure){setError(failure.message);}finally{setBusy(false);}
  }
  if(!rows.length&&!error)return null;
  // Same row look as Today: a dot, what it is, the client and one line, then one button; Dismiss is a quiet link.
  return <section className="rmw-card mrem" data-testid="meeting-reminders" aria-label="Meeting reminders">
    <div className="mrem-head"><h3>Meeting reminders</h3><span className="rm-small">{rows.length} to look at</span></div>
    {error&&<p role="alert" className="rm-error">{error}</p>}
    <ul className="mrem-list">{rows.map(item=>{const prep=item.kind==='preparation';return <li key={item.reminder_id} className="mrem-row" data-testid="meeting-reminder" data-reminder-id={item.reminder_id}>
      <span className={`rm-dot ${prep?'meeting':'late'}`} aria-hidden="true"/>
      <div className="mrem-text"><span className="rm-type">{prep?'Prepare':'Document'}</span><strong>{item.client}</strong> · {prep?'Prepare for your meeting':'Transcript still needed'}
        <small className="rmw-why">Meeting {formatDateTime(item.starts_at)} · {prep?'review the brief and discussion pack before you meet.':'add the transcript or your notes so the minutes can be drafted.'}</small>
        {item.error_code&&<small className="rmw-warn">{item.error_code.replaceAll('_',' ')}. Check the meeting’s recovery steps.</small>}</div>
      <div className="mrem-actions"><button type="button" className="bank-chat-logout" data-testid="meeting-reminder-open" disabled={busy} onClick={()=>onOpen(item.meeting_id,item.client)}>{prep?'Open brief':'Document it'}</button>
        <button type="button" className="link-button" data-testid="meeting-reminder-acknowledge" disabled={busy} onClick={()=>acknowledge(item.reminder_id)}>Dismiss</button></div>
    </li>;})}</ul></section>;
}
