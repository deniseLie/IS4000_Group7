import {useEffect,useState} from 'react';
import Icon from '../Icon';
import {DECLINE_REASONS,FORMAT_WORDS,MOVE_REASONS,WEEKDAY_NAMES,dayCells,decisionBody,decisionNotice,freeTimes,groupByDate,idKeeper,isNewRequest,meetingsPerWeek,
  moveNotice,needsAnswer,plusMinutes,requestLabel,rmDelete,rmGet,rmPost,rmPut,sgtInput,sgtInstant,slotProblemText,takeScheduleTab,topicLabel,weekStart} from '../../lib/rm';
import {formatDate} from '../../lib/format';

const HOURS=Array.from({length:29},(_,i)=>`${String(7+Math.floor(i/2)).padStart(2,'0')}:${i%2?'30':'00'}`);
const TABS=[['appointments','Appointments'],['requests','Requests'],['availability','Availability and hours']];
const REQUEST_VIEWS=[['open','Open'],['scheduled','Scheduled'],['closed','Declined or withdrawn']];

// RM12: confirmed appointments grouped by day, the next 14 days of availability, and weekly working hours.
// Blocking or opening time changes the free times clients see in My requests → Meetings (CR17) straight away.
// The weekly meeting limit per client (0092) is enforced when clients book; here it is shown, with any week over it.
// Requests (build plan 2a, 0106): she accepts one of the client's times, proposes another (held for the client) or
// declines with a reason, and moves a confirmed meeting. Each is one request she presses; SQL checks the slot again.
export default function SchedulePage({token,onAsk,today}) {
  const [tab,setTab]=useState(()=>takeScheduleTab()||'appointments');
  const [appointments,setAppointments]=useState(null),[availability,setAvailability]=useState(null);
  const [requests,setRequests]=useState(null),[shown,setShown]=useState('open'),[toAnswer,setToAnswer]=useState(null);
  const [error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false);
  const [ids]=useState(()=>idKeeper());
  async function load(view=shown) {
    setError('');
    try {
      const auth=await token();
      const [a,v,r]=await Promise.all([rmGet('/appointments',auth),rmGet('/availability',auth),rmGet(`/meeting-requests?status=${view}`,auth)]);
      setAppointments(a.data);setAvailability(v);setRequests(r.data);
      if(view==='open')setToAnswer(r.data.filter(needsAnswer).length);
    } catch(failure) {setError(failure.message);}
  }
  useEffect(()=>{load();},[]);  // eslint-disable-line react-hooks/exhaustive-deps
  async function change(operation,message) {
    setBusy(true);setNotice('');setError('');
    try {await operation(await token());setNotice(message);await load();return true;}
    catch(failure) {setError(failure.message);return false;}
    finally {setBusy(false);}
  }
  // A decision or a move keeps its card's id until SQL records it, so a retry or double click records once. A refusal
  // re-reads the requests and free times and is shown on the card.
  async function act(key,operation,message) {
    setBusy(true);setNotice('');setError('');
    try {const result=await operation(await token(),ids.get(key));ids.done(key);await load();setNotice(message(result));return '';}
    catch(failure) {await load();return failure.message;}
    finally {setBusy(false);}
  }
  const decide=(r,decision,options)=>act(r.request_id,(auth,id)=>rmPost(`/meeting-requests/${r.request_id}/decide`,decisionBody(id,decision,options),auth),decisionNotice);
  const move=(a,startsAt,reason)=>act(a.appointment_id,(auth,id)=>rmPost(`/appointments/${a.appointment_id}/reschedule`,{change_id:id,starts_at:startsAt,reason},auth),
    result=>moveNotice(result,a.client));
  const limit=availability?.rules?.weekly_meetings_per_client;
  const confirmed=(appointments||[]).filter(a=>a.status==='confirmed');
  return <div className="app-page-body rmw-page" data-testid="rm-schedule">
    <header className="page-header"><div><span className="eyebrow">Relationship manager</span><h1>Schedule</h1>
      <p>Your meetings, clients’ meeting requests, the times clients can book over the next 14 days, and your working hours.</p></div>
      <div className="page-header-actions"><button className="bank-chat-logout" onClick={()=>load()} disabled={busy}><Icon name="refresh"/>Refresh</button></div></header>
    <div className="rmw-tabs-row"><nav className="page-tabs" role="tablist">{TABS.map(([key,label])=><button key={key} role="tab" aria-selected={tab===key} data-testid={`rm-schedule-${key}`} onClick={()=>setTab(key)}>{label}
      {key==='appointments'&&appointments&&<span className="count">{confirmed.length}</span>}
      {key==='requests'&&toAnswer!==null&&<span className="count">{toAnswer}</span>}</button>)}</nav>
      {tab==='requests'&&<label className="pill-select rmw-filter" title="Show requests"><Icon name="calendar"/><select aria-label="Show requests" value={shown} disabled={busy}
        onChange={e=>{setShown(e.target.value);setRequests(null);load(e.target.value);}}>{REQUEST_VIEWS.map(([v,l])=><option key={v} value={v}>{l}</option>)}</select><Icon name="chevron"/></label>}</div>
    {limit&&tab!=='requests'&&<p className="rmw-note"><Icon name="info"/>Booking rule: each client can book up to {limit} meetings with you per week ({availability.rules.week}). Clients cannot book more; meetings booked earlier stay.</p>}
    {error&&<p role="alert" className="rm-error">{error}</p>}
    {notice&&<p className="chat-toast" role="status"><Icon name="check"/>{notice}</p>}
    {!appointments&&!error&&<p className="loading" role="status">Loading your schedule…</p>}
    {appointments&&tab==='appointments'&&<Appointments appointments={appointments} limit={limit} today={today} onAsk={onAsk} availability={availability} busy={busy} onMove={move}/>}
    {appointments&&tab==='requests'&&(!requests?<p className="loading" role="status">Loading meeting requests…</p>
      :<MeetingRequests requests={requests} shown={shown} availability={availability} busy={busy} onDecide={decide}/>)}
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

function Appointments({appointments,limit,today,onAsk,availability,busy,onMove}) {
  const [moving,setMoving]=useState(null);
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
        {a.status==='confirmed'&&<div className="rmw-appointment-actions" style={{display:'flex',flexWrap:'wrap',gap:8}}>
          <button className="primary-button small" data-testid="rm-brief" onClick={()=>onAsk(`Brief me for my meeting with ${a.client} on ${a.when}`,
            {intent:'meeting_brief',context:{client_id:a.customer_id,appointment_id:a.appointment_id}})}><Icon name="spark"/>Prepare briefing</button>
          {!a.past&&<button className="bank-chat-logout" data-testid="rm-move" aria-expanded={moving===a.appointment_id} disabled={busy}
            onClick={()=>setMoving(moving===a.appointment_id?null:a.appointment_id)}><Icon name="clock"/>Move</button>}</div>}
        {moving===a.appointment_id&&<MoveForm a={a} free={freeTimes(availability,[a.starts_at])} busy={busy} onMove={onMove} onClose={()=>setMoving(null)}/>}
      </li>)}</ul>
    </section>)}
  </>;
}

// Move a confirmed meeting to another time she agreed with the client (0106 rm_meeting_reschedule).
function MoveForm({a,free,busy,onMove,onClose}) {
  const [at,setAt]=useState(null),[reason,setReason]=useState(MOVE_REASONS[0][0]),[failure,setFailure]=useState('');
  async function submit() {const error=await onMove(a,at,reason);if(error)setFailure(error);else onClose();}
  return <div className="rmw-move" style={{gridColumn:'1/-1'}} data-testid="rm-move-form">
    <div className="rm-form-grid"><TimePicker free={free} onChange={setAt} label="New time"/>
      <label className="form-field">Reason<select value={reason} onChange={e=>setReason(e.target.value)}>{MOVE_REASONS.map(([v,l])=><option key={v} value={v}>{l}</option>)}</select></label></div>
    <p className="rm-small">Agree the new time with {a.client} first. The meeting keeps its purpose and topics, and the client gets an Inbox notice.</p>
    {failure&&<p role="alert" className="rm-error">{failure}</p>}
    <div className="rmw-card-actions"><button className="bank-chat-logout" disabled={busy} onClick={onClose}>Cancel</button>
      <button className="primary-button small" data-testid="rm-move-confirm" disabled={busy||!at} onClick={submit}>Move meeting</button></div>
  </div>;
}

// A free half hour from her availability, or another time typed as Singapore time. SQL checks the time either way.
function TimePicker({free,onChange,label}) {
  const [choice,setChoice]=useState(''),[typed,setTyped]=useState('');
  const pick=value=>{setChoice(value);onChange(value==='other'?sgtInput(typed):value||null);};
  return <>
    <label className="form-field">{label}<select value={choice} onChange={e=>pick(e.target.value)}>
      <option value="">Choose a free time</option>
      {groupByDate(free).map(g=><optgroup key={g.date} label={formatDate(g.date,{weekday:'long'})}>
        {g.items.map(s=><option key={s.starts_at} value={s.starts_at}>{formatDate(g.date,{weekday:'short',year:undefined})}, {s.time}</option>)}</optgroup>)}
      <option value="other">Another time…</option></select></label>
    {choice==='other'&&<label className="form-field">Date and time (Singapore)<input type="datetime-local" step={1800} value={typed}
      onChange={e=>{setTyped(e.target.value);onChange(sgtInput(e.target.value));}}/></label>}
  </>;
}

function MeetingRequests({requests,shown,availability,busy,onDecide}) {
  if(!requests.length)return <p className="page-empty">{shown==='open'?'No meeting requests waiting.':shown==='scheduled'?'No scheduled requests.':'No declined or withdrawn requests.'}</p>;
  return <ul className="rmw-tickets tab-fade" key={shown}>{requests.map(r=><RequestCard key={r.request_id} r={r} free={freeTimes(availability,r.preferred_times.map(t=>t.starts_at))}
    busy={busy} onDecide={onDecide}/>)}</ul>;
}

// One client request. The purpose is the client's own words, shown as text only.
function RequestCard({r,free,busy,onDecide}) {
  const [mode,setMode]=useState(null),[at,setAt]=useState(null),[reason,setReason]=useState(DECLINE_REASONS[0][0]),[failure,setFailure]=useState('');
  async function decide(decision,options) {setFailure('');const error=await onDecide(r,decision,options);setFailure(error);if(!error)setMode(null);}
  const answer=needsAnswer(r),blocked=r.preferred_times.filter(t=>t.problem);
  return <li className={`rmw-ticket${r.overdue?' urgency-urgent':''}`} data-testid="rm-meeting-request" data-request={r.request_id}>
    <div className="rmw-ticket-head"><strong>{r.client}</strong>
      {isNewRequest(r)&&<span className="rmw-chip info">New</span>}
      <span className={`rmw-chip${r.overdue||r.proposal_lapsed?' alert':''}`}>{requestLabel(r)}</span></div>
    <p className="rmw-ticket-meta"><span>{FORMAT_WORDS[r.format]||r.format}</span><span>Received {r.created_text}</span>
      {r.status==='requested'&&<span className={r.overdue?'rmw-late':''}>{r.overdue?'Overdue · was due':'Respond by'} {r.respond_by_text}</span>}</p>
    <div className="rm-chips">{r.topics.map(t=><span key={t} className="rmw-chip">{topicLabel(t)}</span>)}</div>
    <p className="rm-small">In the client’s words</p>
    <p className="rm-quote" data-testid="rm-request-purpose">{r.purpose}</p>
    {r.status==='countered'&&r.meeting&&<p className="rmw-ticket-status"><Icon name={r.proposal_lapsed?'alert':'clock'}/>{r.proposal_lapsed
      ?`The client did not answer the time you proposed (${r.meeting.when}). Accept one of their times, propose another or decline.`
      :`You proposed ${r.meeting.when}. It is held for the client until ${r.meeting.hold_expires}.`}</p>}
    {r.status==='scheduled'&&<p className="rmw-ticket-status"><Icon name="check"/>{r.meeting?`Meeting ${r.meeting.status==='confirmed'?'confirmed':r.meeting.status} for ${r.meeting.when}.`:'Meeting no longer in your calendar.'}</p>}
    {r.status==='declined'&&<p className="rmw-ticket-status"><Icon name="info"/>Declined: {Object.fromEntries(DECLINE_REASONS)[r.decline_reason]||r.decline_reason}.</p>}
    {answer&&<>
      <p className="rm-small">The client’s times</p>
      <div className="rm-slots">{r.preferred_times.map(t=><button key={t.starts_at} className="primary-button small" data-testid="rm-accept" disabled={busy||!!t.problem}
        title={t.problem?`Cannot be accepted: ${slotProblemText(t.problem)}`:'Accept this time'} onClick={()=>decide('accept',{startsAt:t.starts_at})}><Icon name="check"/>Accept {t.when}</button>)}</div>
      {blocked.map(t=><p key={t.starts_at} className="rm-small">{t.when} cannot be accepted: {slotProblemText(t.problem)}.</p>)}
      <div className="rm-ticket-actions">
        <button className="bank-chat-logout" aria-expanded={mode==='counter'} disabled={busy} onClick={()=>setMode(mode==='counter'?null:'counter')}><Icon name="calendar"/>Propose a time</button>
        <button className="bank-chat-logout" aria-expanded={mode==='decline'} disabled={busy} onClick={()=>setMode(mode==='decline'?null:'decline')}><Icon name="close"/>Decline</button></div>
      {mode==='counter'&&<div className="rm-form-grid" data-testid="rm-counter-form"><TimePicker free={free} onChange={setAt} label="Time to propose"/>
        <p className="rm-small rm-span">The time is held for the client for up to two business days; they accept or decline it in My requests.</p>
        <div className="rmw-card-actions rm-span"><button className="primary-button small" data-testid="rm-counter-confirm" disabled={busy||!at}
          onClick={()=>decide('counter',{startsAt:at})}>Propose this time</button></div></div>}
      {mode==='decline'&&<div className="rm-form-grid" data-testid="rm-decline-form">
        <label className="form-field">Reason<select value={reason} onChange={e=>setReason(e.target.value)}>{DECLINE_REASONS.map(([v,l])=><option key={v} value={v}>{l}</option>)}</select></label>
        <p className="rm-small rm-span">The client gets an Inbox notice with this reason. No meeting is booked.</p>
        <div className="rmw-card-actions rm-span"><button className="primary-button small" data-testid="rm-decline-confirm" disabled={busy}
          onClick={()=>decide('decline',{reason})}>Decline request</button></div></div>}
    </>}
    {failure&&<p role="alert" className="rm-error">{failure}</p>}
  </li>;
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
