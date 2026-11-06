import MeetingWorkspace from './meetings/MeetingWorkspace';
import {useEffect,useRef,useState} from 'react';
import Icon from './Icon';
import {supabase} from '../lib/supabase';
import {loadAppointments,loadMeetingRequests,loadSlots} from '../lib/relationship';
import {confirmAction,createActionCase,proposeAction} from '../lib/actions';
import {appointmentTime,slotDay,slotTime} from '../lib/appointmentData';
import {FORMATS} from '../lib/relationshipWorkflow';
import {displayName,formatDateTime,formatTime} from '../lib/format';
import {DECLINED,LIMITS,STATUS,TOPICS,isOpen,meetingRequestTarget,openCount,requestAppointmentIds,requestChoices,suggestedTime,toggleTime,toggleTopic} from '../lib/meetingRequests';

// 2a (0106): she asks her relationship manager for a meeting (what it is about, topics, format, up to 3 free times); her RM
// accepts a time, suggests another (held for her to accept or decline) or answers without a meeting. She also confirms or
// releases a time her RM proposed (appointment.confirm / appointment.cancel) and opens Notes and follow-up on a confirmed
// meeting (MeetingWorkspace, 0120-0124). Each change is a confirm-type action (propose, then her own click confirms it;
// receipts in Actions).
const LEAD=2*60*60*1000;  // SQL refuses times within an hour; free times start two hours ahead, as in the chat
function range(days=14) {
  const now=new Date(Date.now()+8*60*60*1000);
  return {from_date:now.toISOString().slice(0,10),to_date:new Date(now.getTime()+(days-1)*86400000).toISOString().slice(0,10),limit:20};
}
async function token() {
  const {data,error}=await supabase.auth.getSession();
  if(error)throw error;
  if(!data.session)throw new Error('Please sign in again to manage your meetings.');
  return data.session.access_token;
}
const LABEL={held:'Held for you',confirmed:'Booked',cancelled:'Cancelled',expired:'Hold expired'};
const PILL={requested:'wait',countered:'wait',scheduled:'ok',declined:'muted',withdrawn:'muted'};
const scope=access=>createActionCase({session_id:crypto.randomUUID(),case_id:crypto.randomUUID()},access);

export default function Meetings({rmName}) {
  const [slots,setSlots]=useState(null),[meetings,setMeetings]=useState(null),[requests,setRequests]=useState(null);
  const [purpose,setPurpose]=useState(''),[topics,setTopics]=useState([]),[format,setFormat]=useState('video'),[times,setTimes]=useState([]);
  const [busy,setBusy]=useState(true),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const active=useRef(null);
  const [workspace,setWorkspace]=useState(null);
  async function load(signal) {
    const access=await token();
    const [free,booked,asked]=await Promise.all([loadSlots(access,range(),signal),loadAppointments(access,signal),loadMeetingRequests(access,signal)]);
    if(!signal?.aborted){setSlots(free.data);setMeetings(booked.data);setRequests(asked.data);}
  }
  async function run(work,done) {
    const controller=new AbortController();active.current?.abort();active.current=controller;setBusy(true);setError('');setNotice('');
    try{const value=await work(controller.signal);await load(controller.signal);if(done&&!controller.signal.aborted)setNotice(typeof done==='function'?done(value):done);}
    catch(failure){if(failure.name!=='AbortError'&&!controller.signal.aborted)setError(failure.message);}
    finally{if(!controller.signal.aborted)setBusy(false);}
  }
  useEffect(()=>{run(async()=>{});return ()=>active.current?.abort();},[]);
  // Propose, then her click confirms it; a receipt that did not succeed means the request or time changed first.
  async function act(type,target,caseId,signal) {
    const access=await token();
    const case_id=caseId||(await scope(access)).case_id;
    const proposed=await proposeAction({command_id:crypto.randomUUID(),case_id,task_id:crypto.randomUUID(),type,target},access,signal);
    const result=await confirmAction(proposed.command.command_id,access,signal);
    if(result.receipt?.outcome!=='succeeded')throw new Error('That changed before it could be saved. Check your meetings and try again.');
    return result.receipt.result;
  }
  const decide=(item,type,done)=>run(signal=>act(type,{appointment_id:item.appointment_id},item.case_id,signal),done);
  const send=event=>{event.preventDefault();
    let target;
    try{target=meetingRequestTarget({advisorId:slots?.[0]?.advisor_id,purpose,topics,format,times});}catch(failure){setNotice('');setError(failure.message);return;}
    run(signal=>act('meeting.request',target,null,signal),result=>{setPurpose('');setTopics([]);setTimes([]);
      return `Sent. Your relationship manager will answer by ${result.respond_by_text} (Singapore time).`;});
  };
  const reply=(request,type,extra,done)=>run(signal=>act(type,{request_id:request.request_id,...extra},null,signal),done);
  const now=Date.now();
  const owned=requestAppointmentIds((requests||[]).filter(item=>item.status==='countered'));
  const upcoming=(meetings||[]).filter(item=>['held','confirmed'].includes(item.status)&&Date.parse(item.ends_at)>now&&!owned.has(item.appointment_id))
    .sort((a,b)=>Date.parse(a.starts_at)-Date.parse(b.starts_at));
  const past=(meetings||[]).filter(item=>!upcoming.includes(item)&&!owned.has(item.appointment_id)).sort((a,b)=>Date.parse(b.starts_at)-Date.parse(a.starts_at)).slice(0,5);
  const open=(requests||[]).filter(isOpen),closed=(requests||[]).filter(item=>!isOpen(item)).slice(0,5);
  const free=(slots||[]).filter(slot=>Date.parse(slot.starts_at)>now+LEAD);
  const days=[];for(const slot of free){const day=slotDay(slot.starts_at),last=days[days.length-1];if(last&&last[0]===day)last[1].push(slot);else days.push([day,[slot]]);}
  const who=rmName?displayName(rmName):'your relationship manager';
  const full=openCount(requests)>=LIMITS.open;
  const request=item=>{const held=suggestedTime(item,meetings),choices=requestChoices(item);
    return <li key={item.request_id} className={`meeting ${item.status==='countered'?'held':''}`} data-testid="meeting-request" data-status={item.status}>
      <span className="meeting-icon" aria-hidden="true"><Icon name="calendar"/></span>
      <div><strong>“{item.purpose}”</strong>
        <small>{item.topics.map(topic=>TOPICS[topic]).join(', ')} · {FORMATS[item.format]}</small>
        <small>Times you suggested: {item.preferred_times.map(time=>appointmentTime(time)).join('; ')}</small>
        {item.status==='requested'&&<small>Your relationship manager answers by {formatDateTime(item.respond_by,{weekday:'short'})}.</small>}
        {item.status==='countered'&&(held?<small data-testid="suggested-time">Suggested instead: {appointmentTime(held.starts_at)} – {slotTime(held.ends_at)}, held for you until {formatTime(held.hold_expires_at)}.</small>
          :<small>Your relationship manager suggested another time. Reload to see it.</small>)}
        {item.status==='declined'&&<small>{DECLINED[item.decline_reason]||'Your relationship manager answered without a meeting.'}</small>}
        {item.status==='scheduled'&&<small>Booked: you can see it under Your meetings.</small>}</div>
      <span className={`act-pill ${PILL[item.status]}`}>{STATUS[item.status]}</span>
      {choices.length>0&&<div className="meeting-actions">
        {choices.includes('accept')&&held&&<button className="primary-button" data-testid="accept-counter" disabled={busy} onClick={()=>reply(item,'meeting.counter_reply',{reply:'accept'},'Meeting booked. You will also see it in your Inbox.')}>Accept new time</button>}
        {choices.includes('decline')&&held&&<button className="bank-chat-logout" data-testid="decline-counter" disabled={busy} onClick={()=>reply(item,'meeting.counter_reply',{reply:'decline'},'You declined the suggested time. The request is closed.')}>Decline</button>}
        {choices.includes('withdraw')&&<button className="bank-chat-logout" data-testid="withdraw-request" disabled={busy} onClick={()=>reply(item,'meeting.withdraw',{},'Request withdrawn.')}>Withdraw</button>}
      </div>}</li>;};
  return <section className="meetings" data-testid="meetings" aria-label="Meetings with your relationship manager">
    {/* Only the first load shows a loading line; later refreshes keep the page in place (buttons are disabled while busy). */}
    {busy&&(slots===null||meetings===null||requests===null)&&<p className="loading" role="status">Loading meetings…</p>}
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
        {item.status==='confirmed'&&<button className="bank-chat-logout" data-testid="open-meeting-workspace" disabled={busy} onClick={()=>setWorkspace(item.appointment_id)}>Notes and follow-up</button>}
        {item.status==='held'&&item.case_id&&<button className="primary-button" data-testid="confirm-meeting" disabled={busy} onClick={()=>decide(item,'appointment.confirm','Meeting booked. You will also see it in your Inbox.')}>Confirm booking</button>}
        <button className="bank-chat-logout" data-testid="cancel-meeting" disabled={busy} onClick={()=>decide(item,'appointment.cancel',item.status==='held'?'Time released.':'Meeting cancelled.')}>{item.status==='held'?'Release':'Cancel meeting'}</button>
      </div></li>)}</ul>
    {requests!==null&&(open.length>0||closed.length>0)&&<>
      <h2 className="meetings-title">Your meeting requests</h2>
      <ul className="meetings-list" data-testid="meeting-requests">{open.map(request)}</ul>
      {closed.length>0&&<details className="act-more"><summary>Answered and withdrawn</summary><ul className="meetings-list past">{closed.map(request)}</ul></details>}</>}
    {slots!==null&&requests!==null&&<form className="meeting-request-form" data-testid="meeting-request-form" onSubmit={send}>
      <h2 className="meetings-title">Request a meeting with {who}</h2>
      {full&&<p className="page-empty" data-testid="meeting-requests-full">You have {LIMITS.open} open meeting requests, the most at one time. Withdraw one or wait for an answer to ask again.</p>}
      <label className="meeting-purpose">What would you like to discuss?
        <textarea data-testid="meeting-purpose" rows={2} maxLength={LIMITS.purpose} value={purpose} disabled={busy||full} onChange={event=>setPurpose(event.target.value)}
          placeholder="For example: refinancing my home loan"/>
        <small>{purpose.length}/{LIMITS.purpose}. Please don’t include card numbers, passwords or codes.</small></label>
      <fieldset className="meeting-topics" disabled={busy||full}><legend>Topics (up to {LIMITS.topics})</legend>
        {Object.entries(TOPICS).map(([code,label])=><label key={code}><input type="checkbox" data-testid="meeting-topic" value={code} checked={topics.includes(code)}
          onChange={()=>setTopics(toggleTopic(topics,code))}/>{label}</label>)}</fieldset>
      <label className="meetings-format">How would you like to meet? <select data-testid="meeting-format" value={format} disabled={busy||full} onChange={event=>setFormat(event.target.value)}>
        {Object.entries(FORMATS).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label>
      <p className="meeting-times-hint">Pick up to {LIMITS.times} times that suit you (Singapore time). Your relationship manager accepts one, suggests another or replies in a message, usually within one business day. Picked: {times.length}/{LIMITS.times}.</p>
      {!free.length&&<p className="page-empty">No free times in the next 14 days. Use Talk to a person to ask for another time.</p>}
      <div className="meeting-days">{days.map(([day,list])=><div key={day} className="meeting-day"><h3>{day}</h3>
        <div className="meeting-slots">{list.map(slot=>{const picked=times.includes(slot.starts_at);
          return <button type="button" key={slot.starts_at} className={picked?'primary-button':'bank-chat-logout'} data-testid="meeting-slot" aria-pressed={picked}
            disabled={busy||full||(!picked&&times.length>=LIMITS.times)} onClick={()=>setTimes(toggleTime(times,slot.starts_at))}>{slotTime(slot.starts_at)}</button>;})}</div></div>)}</div>
      <button type="submit" className="primary-button" data-testid="send-meeting-request" disabled={busy||full||!free.length}>Send request</button>
    </form>}
    {workspace&&<MeetingWorkspace meetingId={workspace} token={token} onClose={()=>setWorkspace(null)}/>}
    {past.length>0&&<details className="act-more"><summary>Past and cancelled</summary><ul className="meetings-list past">{past.map(item=><li key={item.appointment_id} className="meeting"><div><strong>{appointmentTime(item.starts_at)}</strong><small>{FORMATS[item.format]}</small>{item.status==='confirmed'&&<button className="bank-chat-logout" data-testid="open-past-meeting-workspace" onClick={()=>setWorkspace(item.appointment_id)}>Notes and follow-up</button>}</div><span className="act-pill muted">{LABEL[item.status]||item.status}</span></li>)}</ul></details>}
  </section>;
}
