import {useEffect,useRef,useState} from 'react';
import RequestIntake from './RequestIntake';
import {supabase} from '../lib/supabase';
import {holdAppointment,loadAppointments,loadRequests,loadSlots} from '../lib/relationship';
import {proposeAction} from '../lib/actions';
import {appointmentTime,slotDay,slotTime} from '../lib/appointmentData';

function initialRange() {
  const now=new Date(Date.now()+8*60*60*1000);
  return {from_date:now.toISOString().slice(0,10),to_date:new Date(now.getTime()+6*24*60*60*1000).toISOString().slice(0,10),limit:10};
}
async function token() {
  const {data,error}=await supabase.auth.getSession();
  if(error)throw error;
  if(!data.session)throw new Error('Please sign in again to view relationship support.');
  return data.session.access_token;
}
async function records(range,accessToken,signal) {
  const [requests,slots,appointments]=await Promise.all([loadRequests(accessToken,signal),loadSlots(accessToken,range,signal),loadAppointments(accessToken,signal)]);
  return {requests,slots,appointments};
}

export default function RelationshipDetails({recipient,recipientBusy,onReviewActions}) {
  const [query,setQuery]=useState(initialRange);
  const [range,setRange]=useState(query);
  const [result,setResult]=useState(null);
  const [outcome,setOutcome]=useState(null);
  const [format,setFormat]=useState('video');
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const active=useRef(null);
  useEffect(()=>{
    const controller=new AbortController();active.current=controller;
    setResult(null);setOutcome(null);setError('');setBusy(true);
    async function load() {
      try {
        const loaded=await records(range,await token(),controller.signal);
        if(!controller.signal.aborted)setResult(loaded);
      } catch(failure) {if(failure.name!=='AbortError'&&!controller.signal.aborted)setError(failure.message);}
      finally {if(!controller.signal.aborted)setBusy(false);}
    }
    load();
    return ()=>active.current?.abort();
  },[range]);
  async function holdSlot(slot) {
    const controller=new AbortController();active.current=controller;setBusy(true);setError('');setOutcome(null);
    try {
      const accessToken=await token();
      const saved=await holdAppointment({command_id:crypto.randomUUID(),session_id:crypto.randomUUID(),case_id:crypto.randomUUID(),
        target:{...slot,format}},accessToken,controller.signal);
      if(!controller.signal.aborted)setOutcome(saved);
      const loaded=await records(range,accessToken,controller.signal);
      if(!controller.signal.aborted)setResult(loaded);
    } catch(failure) {if(failure.name!=='AbortError'&&!controller.signal.aborted){setError(failure.message);setResult(null);}}
    finally {if(!controller.signal.aborted)setBusy(false);}
  }
  async function prepareProposal(query) {
    const controller=new AbortController();active.current=controller;setBusy(true);setError('');setOutcome(null);
    try {
      const saved=await proposeAction(query,await token(),controller.signal);
      if(!controller.signal.aborted)setOutcome(saved);
    } catch(failure) {if(failure.name!=='AbortError'&&!controller.signal.aborted){setError(failure.message);setResult(null);}}
    finally {if(!controller.signal.aborted)setBusy(false);}
  }
  function proposal(item,type) {
    return {command_id:crypto.randomUUID(),case_id:item.case_id,task_id:crypto.randomUUID(),type,target:{appointment_id:item.appointment_id}};
  }
  const deciding=outcome?.command.state==='proposed';
  return <section aria-label="Relationship support" className="relationship-details">
    <h3>Requests and appointments</h3>
    <p>Times are shown in Singapore time. A suggestion is not a reservation. Holding a slot reserves it for ten minutes; booking and cancellation require a separate review in Actions.</p>
    <form className="relationship-range" onSubmit={event=>{event.preventDefault();setRange({...query});}}>
      <label>From<input type="date" aria-label="Appointment search start" data-testid="slots-from" required value={query.from_date} onChange={event=>setQuery({...query,from_date:event.target.value})}/></label>
      <label>Through<input type="date" aria-label="Appointment search end" data-testid="slots-to" required value={query.to_date} onChange={event=>setQuery({...query,to_date:event.target.value})}/></label>
      <button className="primary-button" data-testid="refresh-relationship" disabled={busy} type="submit">Refresh</button>
    </form>
    {busy&&<p className="loading" role="status">Checking relationship records… Closing this panel does not cancel a submitted action. Reopen it to check the recorded result.</p>}
    {error&&<p role="alert" data-testid="relationship-error">{error} Refresh, then try again.</p>}
    {outcome&&<div data-testid="booking-outcome" className="command-review">
      <p>{outcome.command.state==='proposed'?'The proposal is saved. Review its date, time and format in Actions before confirming.':outcome.receipt?.outcome==='succeeded'?'A ten-minute hold was recorded. Review the current appointment below before booking.':'The slot could not be held because its availability changed. Refresh and select an available time.'}</p>
      {outcome.receipt&&<p>Receipt: <code>{outcome.receipt.receipt_id}</code></p>}
      {deciding&&<button className="primary-button" data-testid="review-booking-actions" disabled={busy} onClick={onReviewActions}>Review in Actions</button>}
    </div>}
    {result&&<>
      <RequestIntake recipient={recipient} recipientBusy={recipientBusy} onReviewActions={onReviewActions}/>
      <h4>Your requests</h4>
      {!result.requests.data.length&&<p data-testid="requests-empty">No requests were found for your account.</p>}
      <ul className="relationship-list" data-testid="relationship-requests">{result.requests.data.map(item=><li key={item.request_id} data-testid="relationship-request" data-request-id={item.request_id}><strong>{item.who}</strong><p>{item.what}</p><span>Status: {item.status} · Urgency: {item.urgency}{item.deadline&&` · Due: ${item.deadline}`}{item.country&&` · Country reference: ${item.country}`}{item.currency&&` · Currency reference: ${item.currency}`}</span></li>)}</ul>
      <h4>Current appointments</h4>
      {!result.appointments.data.length&&<p data-testid="appointments-empty">No appointments are recorded.</p>}
      <ul className="relationship-list" data-testid="relationship-appointments">{result.appointments.data.map(item=><li key={item.appointment_id} data-appointment-id={item.appointment_id}>
        <p>{appointmentTime(item.starts_at)} – {appointmentTime(item.ends_at)} · {item.format.replaceAll('_',' ')}</p>
        <p>Current status: <strong data-testid="appointment-status">{item.status}</strong></p>
        {item.status==='held'&&<p>Hold expires: {appointmentTime(item.hold_expires_at)}. The server checks expiry before booking.</p>}
        {item.status==='confirmed'&&<p>No real meeting or joining link is connected to this coursework demo.</p>}
        {item.case_id&&['held','confirmed'].includes(item.status)&&<div className="panel-actions">
          {item.status==='held'&&<button className="primary-button" data-testid="prepare-booking-confirm" disabled={busy||!!error||deciding} onClick={()=>prepareProposal(proposal(item,'appointment.confirm'))}>Review booking</button>}
          <button className="bank-chat-logout" data-testid="prepare-booking-cancel" disabled={busy||!!error||deciding} onClick={()=>prepareProposal(proposal(item,'appointment.cancel'))}>Review cancellation</button>
        </div>}
      </li>)}</ul>
      <h4>Suggested appointment times</h4>
      <label>Meeting format <select data-testid="appointment-format" value={format} disabled={busy||deciding} onChange={event=>setFormat(event.target.value)}><option value="video">Video</option><option value="phone">Phone</option><option value="in_person">In person</option></select></label>
      {!result.slots.data.length&&<p data-testid="slots-empty">No available times were found in this range for an assigned manager.</p>}
      <ul className="relationship-list slot-grid" data-testid="relationship-slots">{result.slots.data.map(item=><li key={`${item.advisor_id}:${item.starts_at}`} data-testid="relationship-slot"><p><strong>{slotDay(item.starts_at)}</strong> <time dateTime={item.starts_at}>{slotTime(item.starts_at)}</time> – <time dateTime={item.ends_at}>{slotTime(item.ends_at)}</time></p><button className="bank-chat-logout" data-testid="hold-appointment" disabled={busy||!!error||deciding} onClick={()=>holdSlot(item)}>Hold for ten minutes</button></li>)}</ul>
      <p className="source-notice">Appointments are recorded within this demo. Holding or booking a time does not grant consent to share an agenda or customer data.</p>
    </>}
  </section>;
}
