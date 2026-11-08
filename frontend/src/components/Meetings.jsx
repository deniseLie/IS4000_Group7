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

export default function Meetings({rmName,header=null}) {
  const [slots,setSlots]=useState(null),[meetings,setMeetings]=useState(null),[requests,setRequests]=useState(null);
  const [purpose,setPurpose]=useState(''),[topics,setTopics]=useState([]),[format,setFormat]=useState('video'),[times,setTimes]=useState([]);
  const [busy,setBusy]=useState(true),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const active=useRef(null);
  const [workspace,setWorkspace]=useState(null),[dayPick,setDayPick]=useState(null);
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
    // What it's about is optional in the form: without her own words, the topic names are the purpose.
    const about=purpose.trim()||topics.map(topic=>TOPICS[topic]).join(', ');
    try{target=meetingRequestTarget({advisorId:slots?.[0]?.advisor_id,purpose:about,topics,format,times});}catch(failure){setNotice('');setError(failure.message);return;}
    run(signal=>act('meeting.request',target,null,signal),result=>{setPurpose('');setTopics([]);setTimes([]);
      return `Sent. ${First} will answer by ${result.respond_by_text} (Singapore time).`;});
  };
  const reply=(request,type,extra,done)=>run(signal=>act(type,{request_id:request.request_id,...extra},null,signal),done);
  const now=Date.now();
  const owned=requestAppointmentIds((requests||[]).filter(item=>item.status==='countered'));
  const upcoming=(meetings||[]).filter(item=>['held','confirmed'].includes(item.status)&&Date.parse(item.ends_at)>now&&!owned.has(item.appointment_id))
    .sort((a,b)=>Date.parse(a.starts_at)-Date.parse(b.starts_at));
  const past=(meetings||[]).filter(item=>!upcoming.includes(item)&&!owned.has(item.appointment_id)).sort((a,b)=>Date.parse(b.starts_at)-Date.parse(a.starts_at)).slice(0,5);
  const open=(requests||[]).filter(isOpen),closed=(requests||[]).filter(item=>!isOpen(item)).slice(0,5);
  const answer=open.filter(item=>item.status==='countered'),waiting=open.filter(item=>item.status!=='countered');
  const free=(slots||[]).filter(slot=>Date.parse(slot.starts_at)>now+LEAD);
  const days=[];for(const slot of free){const day=slotDay(slot.starts_at),last=days[days.length-1];if(last&&last[0]===day)last[1].push(slot);else days.push([day,[slot]]);}
  const shownDay=days.find(([day])=>day===dayPick)||days[0];
  const who=rmName?displayName(rmName):'your relationship manager',first=rmName?displayName(rmName).split(' ')[0]:'your relationship manager',First=first.charAt(0).toUpperCase()+first.slice(1);
  const full=openCount(requests)>=LIMITS.open;
  const hour=value=>Number(new Intl.DateTimeFormat('en-SG',{hour:'numeric',hour12:false,timeZone:'Asia/Singapore'}).format(new Date(value)));
  const next=upcoming[0],more=upcoming.slice(1);
  if(workspace)return <section className="meetings" data-testid="meetings" aria-label="Meeting">
    <nav className="meeting-crumbs" aria-label="Breadcrumb"><button type="button" onClick={()=>{setWorkspace(null);run(async()=>{});}}>Meetings</button><span aria-hidden="true">›</span><span aria-current="page">This meeting</span></nav>
    <MeetingWorkspace meetingId={workspace} token={token} rmName={rmName} onClose={()=>setWorkspace(null)}/></section>;
  const meetingRow=item=><li key={item.appointment_id} className={`meeting ${item.status}`} data-appointment-id={item.appointment_id}>
    <span className="meeting-icon" aria-hidden="true"><Icon name="calendar"/></span>
    <div><strong>{appointmentTime(item.starts_at)} – {slotTime(item.ends_at)}</strong><small>{FORMATS[item.format]}{item.purpose?` · ${item.purpose.length>60?`${item.purpose.slice(0,57)}…`:item.purpose}`:''}</small></div>
    <span className={`act-pill ${item.status==='confirmed'?'ok':'wait'}`}>{LABEL[item.status]}</span>
    <div className="meeting-actions">{item.status==='confirmed'&&<button className="bank-chat-logout" data-testid="open-meeting-workspace" disabled={busy} onClick={()=>setWorkspace(item.appointment_id)}>Open meeting</button>}
      {item.status==='held'&&item.case_id&&<button className="primary-button" data-testid="confirm-meeting" disabled={busy} onClick={()=>decide(item,'appointment.confirm','Meeting booked. You will also see it in your Inbox.')}>Confirm booking</button>}
      <button className="link-button" data-testid="cancel-meeting" disabled={busy} onClick={()=>decide(item,'appointment.cancel',item.status==='held'?'Time released.':'Meeting cancelled.')}>{item.status==='held'?'Release':'Cancel'}</button></div></li>;
  return <section className="meetings mtg-page" data-testid="meetings" aria-label={`Meetings with ${who}`}>{header}
    {/* Only the first load shows a loading line; later refreshes keep the page in place (buttons are disabled while busy). */}
    {busy&&(slots===null||meetings===null||requests===null)&&<p className="loading" role="status">Loading meetings…</p>}
    {error&&<p role="alert" className="rm-error">{error}</p>}
    {notice&&<p role="status" className="meetings-notice"><Icon name="check"/>{notice}</p>}
    <div className="mtg-grid"><div className="mtg-main">
      {meetings!==null&&!next&&<p className="page-empty" data-testid="meetings-empty">No upcoming meetings. Ask {first} for one on the right.</p>}
      {next&&<section className={`mtg-next ${next.status}`} aria-label="Your next meeting" data-testid="meetings-upcoming" data-appointment-id={next.appointment_id}>
        <div className="mtg-next-head"><span className="eyebrow">Your next meeting</span><span className={`act-pill ${next.status==='confirmed'?'ok':'wait'}`}>{LABEL[next.status]}</span></div>
        <strong className="mtg-when">{appointmentTime(next.starts_at)} – {slotTime(next.ends_at)}</strong>
        <span className="muted">{FORMATS[next.format]} with {who}{next.purpose?` · ${next.purpose.length>70?`${next.purpose.slice(0,67)}…`:next.purpose}`:''}</span>
        {next.status==='held'&&<p className="rm-small">Held for you until {formatTime(next.hold_expires_at)}. It isn’t booked until you confirm.</p>}
        <div className="meeting-actions">
          {next.status==='confirmed'&&<button className="primary-button" data-testid="open-meeting-workspace" disabled={busy} onClick={()=>setWorkspace(next.appointment_id)}>Open meeting</button>}
          {next.status==='held'&&next.case_id&&<button className="primary-button" data-testid="confirm-meeting" disabled={busy} onClick={()=>decide(next,'appointment.confirm','Meeting booked. You will also see it in your Inbox.')}>Confirm booking</button>}
          <button className="link-button" data-testid="cancel-meeting" disabled={busy} onClick={()=>decide(next,'appointment.cancel',next.status==='held'?'Time released.':'Meeting cancelled.')}>{next.status==='held'?'Release this time':'Cancel meeting'}</button></div>
        {next.status==='confirmed'&&<p className="rm-small">Open the meeting to join the call, see what {first} will cover and, afterwards, the recap and your next steps.</p>}
      </section>}
      {more.length>0&&<section className="mtg-block" aria-label="Also booked"><h2 className="meetings-title">Also coming up</h2><ul className="meetings-list">{more.map(meetingRow)}</ul></section>}

      {answer.map(item=>{const held=suggestedTime(item,meetings),choices=requestChoices(item);
        return <section key={item.request_id} className="mtg-answer" data-testid="meeting-request" data-status={item.status} aria-label="Needs your answer">
          <span className="eyebrow">Needs your answer</span>
          <strong>{First} suggested another time for “{item.purpose}”</strong>
          {held?<span data-testid="suggested-time">{appointmentTime(held.starts_at)} – {slotTime(held.ends_at)} · held for you until {formatTime(held.hold_expires_at)}</span>
            :<span>Refresh to see the suggested time.</span>}
          <div className="meeting-actions">
            {choices.includes('accept')&&held&&<button className="primary-button" data-testid="accept-counter" disabled={busy} onClick={()=>reply(item,'meeting.counter_reply',{reply:'accept'},'Meeting booked. You will also see it in your Inbox.')}>Accept {appointmentTime(held.starts_at)}</button>}
            {choices.includes('decline')&&held&&<button className="bank-chat-logout" data-testid="decline-counter" disabled={busy} onClick={()=>reply(item,'meeting.counter_reply',{reply:'decline'},'You declined the suggested time. The request is closed.')}>No, close this request</button>}
          </div></section>;})}

      {waiting.length>0&&<section className="mtg-block" aria-label={`Waiting for ${first}`}><h2 className="meetings-title">Waiting for {first}</h2>
        <ul className="mtg-rows" data-testid="meeting-requests">{waiting.map(item=>{const choices=requestChoices(item);return <li key={item.request_id} data-testid="meeting-request" data-status={item.status}>
          <span className="rm-dot" aria-hidden="true"/>
          <span className="mtg-row-text"><strong>{item.purpose}</strong><small>{item.topics.map(topic=>TOPICS[topic]).join(', ')} · {FORMATS[item.format]} · you suggested {item.preferred_times.map(time=>appointmentTime(time)).join('; ')}</small>
            <small>{First} answers by {formatDateTime(item.respond_by,{weekday:'short'})}</small></span>
          {choices.includes('withdraw')&&<button className="link-button" data-testid="withdraw-request" disabled={busy} onClick={()=>reply(item,'meeting.withdraw',{},'Request withdrawn.')}>Withdraw</button>}</li>;})}</ul></section>}

      {(past.length>0||closed.length>0)&&<details className="act-more mtg-past"><summary>Past meetings and answered requests</summary>
        <ul className="meetings-list past">{past.map(item=><li key={item.appointment_id} className="meeting"><div><strong>{appointmentTime(item.starts_at)}</strong><small>{FORMATS[item.format]}{item.purpose?` · ${item.purpose}`:''}</small></div>
          <span className="act-pill muted">{LABEL[item.status]||item.status}</span>
          {item.status==='confirmed'&&<button className="bank-chat-logout" data-testid="open-past-meeting-workspace" onClick={()=>setWorkspace(item.appointment_id)}>View recap</button>}</li>)}
          {closed.map(item=><li key={item.request_id} className="meeting" data-testid="meeting-request" data-status={item.status}><div><strong>“{item.purpose}”</strong>
            <small>{item.status==='declined'?DECLINED[item.decline_reason]||`${First} answered without a meeting.`:item.status==='scheduled'?'Booked':STATUS[item.status]}</small></div>
            <span className={`act-pill ${PILL[item.status]}`}>{STATUS[item.status]}</span></li>)}</ul></details>}
    </div>

    {slots!==null&&requests!==null&&<form className="mtg-request" data-testid="meeting-request-form" onSubmit={send} aria-label={`Request a meeting with ${who}`}>
      <div><h2>Request a meeting with {first}</h2><p className="rm-small">3 quick steps. {First} usually answers within one business day.</p></div>
      {full&&<p className="page-empty" data-testid="meeting-requests-full">You have {LIMITS.open} open meeting requests, the most at one time. Withdraw one or wait for an answer to ask again.</p>}
      <fieldset className="mtg-step" disabled={busy||full}><legend><span className="mtg-n">1</span>What about?</legend>
        <div className="mtg-chips">{Object.entries(TOPICS).map(([code,label])=><label key={code} className={`mtg-chip ${topics.includes(code)?'on':''}`}><input type="checkbox" data-testid="meeting-topic" value={code} checked={topics.includes(code)}
          onChange={()=>setTopics(toggleTopic(topics,code))}/>{label}</label>)}</div>
        <label className="mtg-field">Anything {first} should know? <span className="muted">(optional)</span>
          <input data-testid="meeting-purpose" maxLength={LIMITS.purpose} value={purpose} onChange={event=>setPurpose(event.target.value)} placeholder="For example: buying a condo next year"/>
          <small>Please don’t include card numbers, passwords or codes.</small></label></fieldset>
      <fieldset className="mtg-step" disabled={busy||full}><legend><span className="mtg-n">2</span>How?</legend>
        <select data-testid="meeting-format" aria-label="How would you like to meet?" value={format} onChange={event=>setFormat(event.target.value)}>
          {Object.entries(FORMATS).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></fieldset>
      <fieldset className="mtg-step" disabled={busy||full}><legend><span className="mtg-n">3</span>When? <span className="muted">pick up to {LIMITS.times} · Singapore time</span></legend>
        {!free.length&&<p className="page-empty">No free times in the next 14 days. Use Talk to a person to ask for another time.</p>}
        {days.length>0&&<div className="mtg-days" role="group" aria-label="Day">{days.map(([day,list])=><button type="button" key={day} className={`mtg-day ${shownDay?.[0]===day?'on':''}`} aria-pressed={shownDay?.[0]===day}
          onClick={()=>setDayPick(day)}>{day}<small>{list.filter(slot=>times.includes(slot.starts_at)).length?'✓ picked':`${list.length} free`}</small></button>)}</div>}
        {shownDay&&[['Morning',slot=>hour(slot.starts_at)<12],['Afternoon',slot=>hour(slot.starts_at)>=12]].map(([label,keep])=>{const list=shownDay[1].filter(keep);
          return list.length>0&&<div key={label} className="mtg-slots"><span className="eyebrow">{label}</span><div className="meeting-slots">{list.map(slot=>{const picked=times.includes(slot.starts_at);
            return <button type="button" key={slot.starts_at} className={picked?'primary-button':'bank-chat-logout'} data-testid="meeting-slot" aria-pressed={picked}
              disabled={busy||full||(!picked&&times.length>=LIMITS.times)} onClick={()=>setTimes(toggleTime(times,slot.starts_at))}>{slotTime(slot.starts_at)}</button>;})}</div></div>;})}
        <p className="mtg-picked">{times.length?<><strong>Your choices ({times.length} of {LIMITS.times}):</strong> {[...times].sort((a,b)=>Date.parse(a)-Date.parse(b)).map(time=>appointmentTime(time)).join(' · ')}</>:'No times picked yet.'}</p></fieldset>
      <button type="submit" className="primary-button mtg-send" data-testid="send-meeting-request" disabled={busy||full||!free.length||!topics.length||!times.length}>Send request to {first}</button>
    </form>}
    </div>
  </section>;
}
