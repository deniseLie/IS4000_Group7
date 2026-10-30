import {useEffect,useRef,useState} from 'react';
import Icon from './Icon';
import {supabase} from '../lib/supabase';
import {holdAppointment,loadAppointments,loadSlots} from '../lib/relationship';
import {confirmAction,proposeAction} from '../lib/actions';
import {appointmentTime,slotDay,slotTime} from '../lib/appointmentData';
import {FORMATS} from '../lib/relationshipWorkflow';
import {displayName,formatTime} from '../lib/format';

// CR17: book a meeting with the relationship manager. Free times come from the RM's working hours (migrations 0034-0036);
// picking one holds it for 10 minutes, and it is booked only when the client confirms. Two bookings can never share a slot.
function range(days=14) {
  const now=new Date(Date.now()+8*60*60*1000);
  return {from_date:now.toISOString().slice(0,10),to_date:new Date(now.getTime()+(days-1)*86400000).toISOString().slice(0,10),limit:12};
}
async function token() {
  const {data,error}=await supabase.auth.getSession();
  if(error)throw error;
  if(!data.session)throw new Error('Please sign in again to book a meeting.');
  return data.session.access_token;
}
const LABEL={held:'Held for you',confirmed:'Booked',cancelled:'Cancelled',expired:'Hold expired'};

export default function Meetings({rmName}) {
  const [slots,setSlots]=useState(null),[meetings,setMeetings]=useState(null);
  const [format,setFormat]=useState('video'),[busy,setBusy]=useState(true),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const active=useRef(null);
  async function load(signal) {
    const access=await token();
    const [free,booked]=await Promise.all([loadSlots(access,range(),signal),loadAppointments(access,signal)]);
    if(!signal?.aborted){setSlots(free.data);setMeetings(booked.data);}
  }
  async function run(work,done) {
    const controller=new AbortController();active.current?.abort();active.current=controller;setBusy(true);setError('');setNotice('');
    try{await work(controller.signal);await load(controller.signal);if(done&&!controller.signal.aborted)setNotice(done);}
    catch(failure){if(failure.name!=='AbortError'&&!controller.signal.aborted)setError(failure.message);}
    finally{if(!controller.signal.aborted)setBusy(false);}
  }
  useEffect(()=>{run(async()=>{});return ()=>active.current?.abort();},[]);
  const hold=slot=>run(async signal=>{await holdAppointment({command_id:crypto.randomUUID(),session_id:crypto.randomUUID(),case_id:crypto.randomUUID(),target:{...slot,format}},await token(),signal);},
    'Time held for 10 minutes. Confirm it below to book.');
  // Confirming and cancelling are confirm-type actions: propose, then the client's own click confirms it (receipts in Actions).
  const decide=(item,type,done)=>run(async signal=>{const access=await token();
    const proposed=await proposeAction({command_id:crypto.randomUUID(),case_id:item.case_id,task_id:crypto.randomUUID(),type,target:{appointment_id:item.appointment_id}},access,signal);
    const result=await confirmAction(proposed.command.command_id,access,signal);
    if(result.receipt?.outcome!=='succeeded')throw new Error('That time changed before it could be saved. Choose another time.');},done);
  const now=Date.now();
  const upcoming=(meetings||[]).filter(item=>['held','confirmed'].includes(item.status)&&Date.parse(item.ends_at)>now).sort((a,b)=>Date.parse(a.starts_at)-Date.parse(b.starts_at));
  const past=(meetings||[]).filter(item=>!upcoming.includes(item)).sort((a,b)=>Date.parse(b.starts_at)-Date.parse(a.starts_at)).slice(0,5);
  const days=[];for(const slot of slots||[]){const day=slotDay(slot.starts_at),last=days[days.length-1];if(last&&last[0]===day)last[1].push(slot);else days.push([day,[slot]]);}
  const who=rmName?displayName(rmName):'your relationship manager';
  return <section className="meetings" data-testid="meetings" aria-label="Meetings with your relationship manager">
    {/* Only the first load shows a loading line; later refreshes keep the page in place (buttons are disabled while busy). */}
    {busy&&(slots===null||meetings===null)&&<p className="loading" role="status">Loading meetings…</p>}
    {error&&<p role="alert">{error}</p>}
    {notice&&<p role="status" className="meetings-notice"><Icon name="check"/>{notice}</p>}
    {meetings!==null&&<h2 className="meetings-title">Your meetings</h2>}
    {meetings&&!upcoming.length&&<p className="page-empty" data-testid="meetings-empty">No upcoming meetings.</p>}
    <ul className="meetings-list" data-testid="meetings-upcoming">{upcoming.map(item=><li key={item.appointment_id} className={`meeting ${item.status}`} data-appointment-id={item.appointment_id}>
      <span className="meeting-icon" aria-hidden="true"><Icon name="calendar"/></span>
      <div><strong>{appointmentTime(item.starts_at)} – {slotTime(item.ends_at)}</strong><small>With {who} · {FORMATS[item.format]}</small>
        {item.status==='held'&&<small>Held until {formatTime(item.hold_expires_at)}. Not booked yet.</small>}</div>
      <span className={`act-pill ${item.status==='confirmed'?'ok':'wait'}`}>{LABEL[item.status]}</span>
      <div className="meeting-actions">
        {item.status==='held'&&<button className="primary-button" data-testid="confirm-meeting" disabled={busy} onClick={()=>decide(item,'appointment.confirm','Meeting booked. You will also see it in your Inbox.')}>Confirm booking</button>}
        <button className="bank-chat-logout" data-testid="cancel-meeting" disabled={busy} onClick={()=>decide(item,'appointment.cancel',item.status==='held'?'Time released.':'Meeting cancelled.')}>{item.status==='held'?'Release':'Cancel meeting'}</button>
      </div></li>)}</ul>
    {slots!==null&&<>
    <h2 className="meetings-title">Book a meeting with {who}</h2>
    <label className="meetings-format">Meeting type <select data-testid="meeting-format" value={format} disabled={busy} onChange={event=>setFormat(event.target.value)}>
      {Object.entries(FORMATS).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label>
    {slots&&!slots.length&&<p className="page-empty">No free times in the next 14 days. Use Talk to a person to ask for another time.</p>}
    <div className="meeting-days">{days.map(([day,list])=><div key={day} className="meeting-day"><h3>{day}</h3>
      <div className="meeting-slots">{list.map(slot=><button key={slot.starts_at} className="bank-chat-logout" data-testid="meeting-slot" disabled={busy||upcoming.some(item=>item.status==='held')} onClick={()=>hold(slot)}>{slotTime(slot.starts_at)}</button>)}</div></div>)}</div></>}
    {past.length>0&&<details className="act-more"><summary>Past and cancelled</summary><ul className="meetings-list past">{past.map(item=><li key={item.appointment_id} className="meeting"><div><strong>{appointmentTime(item.starts_at)}</strong><small>{FORMATS[item.format]}</small></div><span className="act-pill muted">{LABEL[item.status]||item.status}</span></li>)}</ul></details>}
  </section>;
}
