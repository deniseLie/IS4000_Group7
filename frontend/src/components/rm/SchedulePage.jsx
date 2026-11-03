import {useEffect,useState} from 'react';
import Icon from '../Icon';
import {FORMAT_WORDS,WEEKDAY_NAMES,dayCells,groupByDate,meetingsPerWeek,plusMinutes,rmDelete,rmGet,rmPost,rmPut,sgtInstant,weekStart} from '../../lib/rm';
import {formatDate} from '../../lib/format';

const HOURS=Array.from({length:29},(_,i)=>`${String(7+Math.floor(i/2)).padStart(2,'0')}:${i%2?'30':'00'}`);
const TABS=[['appointments','Appointments'],['availability','Availability and hours']];

// RM12: confirmed appointments grouped by day, the next 14 days of availability, and weekly working hours.
// Blocking or opening time changes the free times clients see in My requests → Meetings (CR17) straight away.
// The weekly meeting limit per client (0092) is enforced when clients book; here it is shown, with any week over it.
export default function SchedulePage({token,onAsk,today}) {
  const [tab,setTab]=useState('appointments');
  const [appointments,setAppointments]=useState(null),[availability,setAvailability]=useState(null);
  const [error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false);
  async function load() {
    setError('');
    try {
      const auth=await token();
      const [a,v]=await Promise.all([rmGet('/appointments',auth),rmGet('/availability',auth)]);
      setAppointments(a.data);setAvailability(v);
    } catch(failure) {setError(failure.message);}
  }
  useEffect(()=>{load();},[]);  // eslint-disable-line react-hooks/exhaustive-deps
  async function change(operation,message) {
    setBusy(true);setNotice('');setError('');
    try {await operation(await token());setNotice(message);await load();return true;}
    catch(failure) {setError(failure.message);return false;}
    finally {setBusy(false);}
  }
  const limit=availability?.rules?.weekly_meetings_per_client;
  const confirmed=(appointments||[]).filter(a=>a.status==='confirmed');
  return <div className="app-page-body rmw-page" data-testid="rm-schedule">
    <header className="page-header"><div><span className="eyebrow">Relationship manager</span><h1>Schedule</h1>
      <p>Your meetings, the times clients can book over the next 14 days, and your working hours.</p></div>
      <div className="page-header-actions"><button className="bank-chat-logout" onClick={load} disabled={busy}><Icon name="refresh"/>Refresh</button></div></header>
    <div className="rmw-tabs-row"><nav className="page-tabs" role="tablist">{TABS.map(([key,label])=><button key={key} role="tab" aria-selected={tab===key} onClick={()=>setTab(key)}>{label}
      {key==='appointments'&&appointments&&<span className="count">{confirmed.length}</span>}</button>)}</nav></div>
    {limit&&<p className="rmw-note"><Icon name="info"/>Booking rule: each client can book up to {limit} meetings with you per week ({availability.rules.week}). Clients cannot book more; meetings booked earlier stay.</p>}
    {error&&<p role="alert" className="rm-error">{error}</p>}
    {notice&&<p className="chat-toast" role="status"><Icon name="check"/>{notice}</p>}
    {!appointments&&!error&&<p className="loading" role="status">Loading your schedule…</p>}
    {appointments&&tab==='appointments'&&<Appointments appointments={appointments} limit={limit} today={today} onAsk={onAsk}/>}
    {availability&&tab==='availability'&&<div className="rmw-avail-layout">
      <Availability availability={availability} busy={busy} change={change}/>
      <WorkingHours key={JSON.stringify(availability.weekly_hours)} availability={availability} busy={busy} change={change}/></div>}
  </div>;
}

function dayTitle(date,today) {
  if(!today)return formatDate(date,{weekday:'long'});
  const diff=Math.round((Date.parse(date)-Date.parse(today))/86400000);
  return diff===0?`Today · ${formatDate(date)}`:diff===1?`Tomorrow · ${formatDate(date)}`:formatDate(date,{weekday:'long'});
}
function timeOf(when) {return (when||'').split(', ').at(-1);}

function Appointments({appointments,limit,today,onAsk}) {
  if(!appointments.length)return <p className="page-empty">No appointments in the next 14 days.</p>;
  const perWeek=meetingsPerWeek(appointments);
  const over=[...new Map(appointments.filter(a=>limit&&perWeek.get(`${a.customer_id}|${weekStart(a.date)}`)>limit)
    .map(a=>[`${a.customer_id}|${weekStart(a.date)}`,a])).values()];
  return <>
    {over.map(a=><p key={a.customer_id+a.date} className="rmw-warn" role="status"><Icon name="alert"/>{a.client} has {perWeek.get(`${a.customer_id}|${weekStart(a.date)}`)} meetings in the week of {formatDate(weekStart(a.date))}, above the limit of {limit}. They were booked before the limit applied; consider combining them.</p>)}
    {groupByDate(appointments).map(group=><section key={group.date} className="rmw-day-group">
      <h3>{dayTitle(group.date,today)}</h3>
      <ul className="rmw-appointments">{group.items.map(a=><li key={a.appointment_id} className={`rmw-appointment ${a.status}`} data-testid="rm-appointment">
        <span className="rmw-time">{timeOf(a.when)}</span>
        <span className="meeting-icon"><Icon name={a.format==='phone'?'support':a.format==='video'?'chat':'pin'}/></span>
        <div className="rmw-appointment-main"><strong>{a.client}</strong>
          <small>{FORMAT_WORDS[a.format]} · {a.purpose||'No purpose recorded'}{a.status==='held'?` · held until ${a.hold_expires}, not confirmed`:''}</small></div>
        <span className={`ticket-status ${a.status==='held'?'waiting_on_client':'resolved'}`}>{a.status==='held'?'Held':'Confirmed'}</span>
        {a.status==='confirmed'&&<div className="rmw-appointment-actions">
          <button className="primary-button small" data-testid="rm-brief" onClick={()=>onAsk(`Brief me for my meeting with ${a.client} on ${a.when}`,
            {intent:'meeting_brief',context:{client_id:a.customer_id,appointment_id:a.appointment_id}})}><Icon name="spark"/>Prepare briefing</button></div>}
      </li>)}</ul>
    </section>)}
  </>;
}

// One week at a time as a calendar grid: days across, half hours down. Free times can be blocked, blocked times opened.
function Availability({availability,busy,change}) {
  const weeks=[...new Set((availability.days||[]).map(d=>weekStart(d.date)))];
  const [week,setWeek]=useState(()=>{const withWork=weeks.find(w=>availability.days.some(d=>weekStart(d.date)===w&&d.hours&&dayCells(d).some(c=>c.state!=='past')));return withWork||weeks[0];});
  const block=(date,time)=>{const starts=sgtInstant(date,time);return change(auth=>rmPost('/availability/blocks',{starts_at:starts,ends_at:plusMinutes(starts,30),reason:'Unavailable'},auth),`Blocked ${time} on ${formatDate(date)}. Clients can no longer book it.`);};
  const unblock=(id,label)=>change(auth=>rmDelete(`/availability/blocks/${id}`,auth),`Opened ${label} again for client bookings.`);
  const blockDay=day=>{const s=sgtInstant(day.date,day.hours.starts),e=sgtInstant(day.date,day.hours.ends);
    return change(auth=>rmPost('/availability/blocks',{starts_at:new Date(Math.max(Date.parse(s),Math.ceil(Date.now()/1800000)*1800000)).toISOString(),ends_at:e,reason:'Day unavailable'},auth),`Blocked the rest of ${formatDate(day.date)}.`);};
  const days=(availability.days||[]).filter(d=>weekStart(d.date)===week);
  const shown=days.filter(d=>d.hours||d.weekday<=5);  // weekends only when they are working days
  const cells=Object.fromEntries(shown.map(d=>[d.date,Object.fromEntries(dayCells(d).map(c=>[c.time,c]))]));
  const times=[...new Set(shown.flatMap(d=>Object.keys(cells[d.date])))].sort();
  const index=weeks.indexOf(week);
  const label=w=>{const end=new Date(`${w}T00:00:00Z`);end.setUTCDate(end.getUTCDate()+6);return `${formatDate(w,{year:undefined})} – ${formatDate(end.toISOString().slice(0,10))}`;};
  return <section className="rmw-card rmw-week" data-testid="rmw-week">
    <div className="rmw-card-head"><h3><Icon name="calendar"/>Availability</h3>
      <div className="rmw-week-nav"><button className="icon-button" aria-label="Previous week" disabled={index<=0} onClick={()=>setWeek(weeks[index-1])}><Icon name="chevron"/></button>
        <strong>{label(week)}</strong>
        <button className="icon-button" aria-label="Next week" disabled={index>=weeks.length-1} onClick={()=>setWeek(weeks[index+1])}><Icon name="chevron"/></button></div></div>
    <div className="rmw-legend-keys"><span className="cell free">Free</span><span className="cell booked">Booked</span><span className="cell held">Held</span><span className="cell blocked">Blocked</span><span className="cell past">Past</span></div>
    {!times.length?<p className="rm-none">No working hours this week.</p>:
    <div className="rmw-grid-wrap"><div className="rmw-calendar" style={{gridTemplateColumns:`52px repeat(${shown.length},minmax(64px,1fr))`}}>
      <span/>{shown.map(d=>{const free=Object.values(cells[d.date]).filter(c=>c.state==='free').length;return <div key={d.date} className="rmw-cal-day">
        <strong>{formatDate(d.date,{weekday:'short',year:undefined,month:undefined})}</strong>
        <small>{d.hours?`${free} free`:'Off'}</small>
        {d.hours&&free>0&&<button className="rmw-mini" disabled={busy} title="Block the rest of this day" onClick={()=>blockDay(d)}>Block day</button>}</div>;})}
      {times.map(time=>[<span key={time} className="rmw-cal-time">{time}</span>,...shown.map(d=>{const c=cells[d.date][time];
        if(!c)return <span key={d.date+time} className="rmw-cal-empty"/>;
        const label=c.state==='booked'||c.state==='held'?c.client:c.state==='blocked'?'Blocked':'';
        return <button key={d.date+time} type="button" className={`cell ${c.state}`} disabled={busy||c.state==='booked'||c.state==='held'||c.state==='past'}
          title={`${formatDate(d.date)} ${time}: ${c.state==='booked'||c.state==='held'?`${c.client} (${c.state})`:c.state==='blocked'?(c.reason||'Blocked'):c.state}`}
          aria-label={`${formatDate(d.date)} ${time} ${c.state}${c.client?` – ${c.client}`:''}`}
          onClick={()=>c.state==='free'?block(d.date,time):c.state==='blocked'&&c.block_id?unblock(c.block_id,`${time} on ${formatDate(d.date)}`):null}>{c.state==='booked'||c.state==='held'?c.client.split(' ')[0]:label}</button>;})])}
    </div></div>}
    <p className="rm-small">Select a free time to block it, or a blocked time to open it again. Booked and held times cannot be blocked.</p>
  </section>;
}

function WorkingHours({availability,busy,change}) {
  const initial=Object.fromEntries(availability.weekly_hours.map(h=>[h.weekday,{starts:h.starts||'',ends:h.ends||''}]));
  const [hours,setHours]=useState(initial),[editing,setEditing]=useState(false),[weekday,setWeekday]=useState({starts:'09:00',ends:'17:00'});
  const changed=Object.keys(hours).filter(w=>hours[w].starts!==initial[w].starts||hours[w].ends!==initial[w].ends);
  const set=(w,patch)=>setHours(h=>({...h,[w]:{...h[w],...patch}}));
  async function save() {
    // One save per changed day; the server refuses a change that would leave a confirmed meeting outside the hours.
    const ok=await change(async auth=>{for(const w of changed){const h=hours[w];await rmPut('/availability/hours',{weekday:Number(w),starts:h.starts||null,ends:h.starts?h.ends:null},auth);}},
      `Working hours saved for ${changed.map(w=>WEEKDAY_NAMES[w-1]).join(', ')}.`);
    if(ok)setEditing(false);
  }
  const summary=w=>hours[w].starts?`${hours[w].starts} – ${hours[w].ends}`:'Not working';
  return <section className="rmw-card rmw-hours">
    <div className="rmw-card-head"><div><h3><Icon name="history"/>Working hours</h3><p>Repeat every week. Clients can only book inside them.</p></div>
      {!editing&&<button className="bank-chat-logout" onClick={()=>setEditing(true)}><Icon name="calendar"/>Edit</button>}</div>
    {editing&&<div className="rmw-hours-bulk"><span>Mon–Fri</span>
      <select aria-label="Weekday start" value={weekday.starts} onChange={e=>setWeekday({...weekday,starts:e.target.value})}>{HOURS.map(t=><option key={t}>{t}</option>)}</select>
      <span>to</span><select aria-label="Weekday end" value={weekday.ends} onChange={e=>setWeekday({...weekday,ends:e.target.value})}>{HOURS.map(t=><option key={t}>{t}</option>)}</select>
      <button className="link-button" onClick={()=>[1,2,3,4,5].forEach(w=>set(w,weekday))}>Apply</button></div>}
    <table className="rmw-hours-table"><tbody>{WEEKDAY_NAMES.map((name,i)=>{const w=i+1,h=hours[w];return <tr key={w} className={changed.includes(String(w))?'changed':''}>
      <th scope="row">{name}</th>
      {editing?<><td><select aria-label={`${name} start`} value={h.starts} onChange={e=>set(w,{starts:e.target.value,ends:e.target.value?(h.ends||'17:00'):''})}><option value="">Not working</option>{HOURS.map(t=><option key={t}>{t}</option>)}</select></td>
        <td>{h.starts?<select aria-label={`${name} end`} value={h.ends} onChange={e=>set(w,{ends:e.target.value})}>{HOURS.filter(t=>t>h.starts).map(t=><option key={t}>{t}</option>)}</select>:<span className="rm-none">–</span>}</td></>
        :<td colSpan={2} className={h.starts?'':'rm-none'}>{summary(w)}</td>}
    </tr>;})}</tbody></table>
    {editing&&<div className="rmw-card-actions"><button className="bank-chat-logout" disabled={busy} onClick={()=>{setHours(initial);setEditing(false);}}>Cancel</button>
      <button className="primary-button" disabled={busy||!changed.length} onClick={save}>Save changes{changed.length?` (${changed.length})`:''}</button></div>}
    <p className="rm-small">A change is refused if a confirmed meeting would fall outside the new hours.</p>
  </section>;
}
