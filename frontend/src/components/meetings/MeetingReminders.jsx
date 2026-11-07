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
  return <section className="rmw-card" data-testid="meeting-reminders" aria-label="Meeting reminders"><h3>Meeting reminders</h3>
    {error&&<p role="alert" className="rm-error">{error}</p>}
    <ul className="rmw-list">{rows.map(item=><li key={item.reminder_id} data-testid="meeting-reminder" data-reminder-id={item.reminder_id}>
      <div><strong>{item.client} · {item.kind==='preparation'?'Prepare for your meeting':'Transcript still needed'}</strong>
        <small>Meeting {formatDateTime(item.starts_at)}</small><p className="rm-small">{item.kind==='preparation'
          ?'Review the saved brief, current records and discussion pack before the meeting.'
          :'The transcript deadline has passed. Check My Notes capture, import the saved export, or document your recollection.'}</p>
        {item.error_code&&<p className="rmw-warn">{item.error_code.replaceAll('_',' ')}. Review recovery controls in the meeting workspace.</p>}</div>
      <div className="meeting-actions"><button type="button" className="primary-button small" data-testid="meeting-reminder-open" disabled={busy} onClick={()=>onOpen(item.meeting_id)}>Open meeting</button>
        <button type="button" className="bank-chat-logout" data-testid="meeting-reminder-acknowledge" disabled={busy} onClick={()=>acknowledge(item.reminder_id)}>Dismiss reminder</button></div>
    </li>)}</ul></section>;
}
