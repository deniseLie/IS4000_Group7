import {useEffect,useState} from 'react';
import Icon from '../Icon';
import {FORMAT_WORDS,WEEKDAY_NAMES,dayCells,plusMinutes,rmDelete,rmGet,rmPost,rmPut,sgtInstant} from '../../lib/rm';
import {formatDate} from '../../lib/format';

const HOURS=Array.from({length:29},(_,i)=>`${String(7+Math.floor(i/2)).padStart(2,'0')}:${i%2?'30':'00'}`);

// RM12: confirmed appointments (client, date, time, format, status, purpose) and the next 14 days of availability.
// Blocking or opening time here changes the free times clients see in My requests → Meetings (CR17) straight away.
export default function SchedulePage({token,onAsk}) {
  const [appointments,setAppointments]=useState(null),[availability,setAvailability]=useState(null);
  const [error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false),[hours,setHours]=useState(null);
  async function load() {
    setError('');
    try {
      const auth=await token();
      const [a,v]=await Promise.all([rmGet('/appointments',auth),rmGet('/availability',auth)]);
      setAppointments(a.data);setAvailability(v);
      setHours(Object.fromEntries(v.weekly_hours.map(h=>[h.weekday,{starts:h.starts||'',ends:h.ends||''}])));
    } catch(failure) {setError(failure.message);}
  }
  useEffect(()=>{load();},[]);  // eslint-disable-line react-hooks/exhaustive-deps
  async function change(operation,message) {
    setBusy(true);setNotice('');setError('');
    try {await operation(await token());setNotice(message);await load();}
    catch(failure) {setError(failure.message);}
    finally {setBusy(false);}
  }
  const block=(date,time)=>{const starts=sgtInstant(date,time);return change(auth=>rmPost('/availability/blocks',{starts_at:starts,ends_at:plusMinutes(starts,30),reason:'Unavailable'},auth),`Blocked ${time} on ${formatDate(date)}. Clients can no longer book it.`);};
  const unblock=(id,label)=>change(auth=>rmDelete(`/availability/blocks/${id}`,auth),`Opened ${label} again for client bookings.`);
  const blockDay=day=>{const s=sgtInstant(day.date,day.hours.starts),e=sgtInstant(day.date,day.hours.ends);
    return change(auth=>rmPost('/availability/blocks',{starts_at:new Date(Math.max(Date.parse(s),Math.ceil(Date.now()/1800000)*1800000)).toISOString(),ends_at:e,reason:'Day unavailable'},auth),`Blocked the rest of ${formatDate(day.date)}.`);};
  const saveHours=weekday=>{const h=hours[weekday];return change(auth=>rmPut('/availability/hours',{weekday,starts:h.starts||null,ends:h.ends||null},auth),`${WEEKDAY_NAMES[weekday-1]} hours saved.`);};
  return <div className="app-page-body" data-testid="rm-schedule">
    <header className="page-header"><div><span className="eyebrow">Relationship manager</span><h1>Schedule</h1>
      <p>Your confirmed meetings and the times clients can book over the next 14 days.</p></div>
      <div className="page-header-actions"><button className="bank-chat-logout" onClick={load} disabled={busy}><Icon name="refresh"/>Refresh</button></div></header>
    {error&&<p role="alert" className="rm-error">{error}</p>}
    {notice&&<p className="chat-toast" role="status"><Icon name="check"/>{notice}</p>}
    <h2 className="meetings-title">Appointments</h2>
    {!appointments&&!error&&<p className="loading" role="status">Loading your schedule…</p>}
    {appointments&&!appointments.length&&<p className="page-empty">No appointments in the next 14 days.</p>}
    <ul className="meetings-list">{(appointments||[]).map(a=><li key={a.appointment_id} className={`meeting ${a.status}`} data-testid="rm-appointment">
      <span className="meeting-icon"><Icon name={a.format==='phone'?'support':a.format==='video'?'chat':'pin'}/></span>
      <div><strong>{a.client} · {a.when}</strong><small>{FORMAT_WORDS[a.format]} · {a.status==='held'?`Held until ${a.hold_expires} – not confirmed`:'Confirmed'} · {a.purpose||'Purpose not recorded'}</small></div>
      <span className={`ticket-status ${a.status==='held'?'waiting_on_client':'resolved'}`}>{a.status==='held'?'Held':'Confirmed'}</span>
      {a.status==='confirmed'&&<div className="meeting-actions"><button className="primary-button" data-testid="rm-brief" onClick={()=>onAsk(`Brief me for my meeting with ${a.client} on ${a.when}`,
        {intent:'meeting_brief',context:{client_id:a.customer_id,appointment_id:a.appointment_id}})}><Icon name="spark"/>Prepare briefing</button>
        <button className="bank-chat-logout" onClick={()=>onAsk(`What is this meeting with ${a.client} about?`,{intent:'schedule',context:{client_id:a.customer_id,appointment_id:a.appointment_id}})}>Ask about it</button></div>}
    </li>)}</ul>
    <h2 className="meetings-title">Availability, next 14 days</h2>
    <p className="rm-small">Tap a free time to block it, or a blocked time to open it again. Times with a client booking cannot be blocked.</p>
    <div className="rm-legend"><span className="cell free">Free</span><span className="cell booked">Booked</span><span className="cell held">Held</span><span className="cell blocked">Blocked</span></div>
    <div className="rm-days">{(availability?.days||[]).map(day=>{const cells=dayCells(day);return <div key={day.date} className="rmw-day" data-testid="rmw-day">
      <div className="rmw-day-head"><strong>{formatDate(day.date,{weekday:'short'})}</strong><small>{day.hours?`${day.hours.starts}–${day.hours.ends}`:'Not working'}</small>
        {day.hours&&cells.some(c=>c.state==='free')&&<button className="link-button" disabled={busy} onClick={()=>blockDay(day)}>Block rest of day</button>}</div>
      <div className="rm-cells">{cells.map(c=><button key={c.time} type="button" className={`cell ${c.state}`} disabled={busy||c.state==='booked'||c.state==='held'||c.state==='past'}
        title={c.state==='booked'||c.state==='held'?`${c.client} (${c.state})`:c.state==='blocked'?(c.reason||'Blocked'):c.state}
        onClick={()=>c.state==='free'?block(day.date,c.time):c.state==='blocked'&&c.block_id?unblock(c.block_id,`${c.time} on ${formatDate(day.date)}`):null}>{c.time}</button>)}</div>
    </div>;})}</div>
    {hours&&<details className="relationship-details rm-hours"><summary>Weekly working hours</summary>
      <p className="rm-small">These hours repeat every week. Changes are refused if a confirmed meeting would fall outside them.</p>
      {WEEKDAY_NAMES.map((name,i)=>{const w=i+1,h=hours[w];return <div key={w} className="rm-hours-row"><span>{name}</span>
        <select aria-label={`${name} start`} value={h.starts} onChange={e=>setHours({...hours,[w]:{...h,starts:e.target.value,ends:e.target.value?h.ends||'17:00':''}})}><option value="">Not working</option>{HOURS.map(t=><option key={t}>{t}</option>)}</select>
        <select aria-label={`${name} end`} value={h.ends} disabled={!h.starts} onChange={e=>setHours({...hours,[w]:{...h,ends:e.target.value}})}><option value="">–</option>{HOURS.map(t=><option key={t}>{t}</option>)}</select>
        <button className="bank-chat-logout" disabled={busy} onClick={()=>saveHours(w)}>Save</button></div>;})}</details>}
  </div>;
}
