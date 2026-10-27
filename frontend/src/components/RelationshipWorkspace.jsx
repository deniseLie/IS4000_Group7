import {useEffect,useMemo,useRef,useState} from 'react';
import Icon from './Icon';
import RelationshipConfirmDialog from './RelationshipConfirmDialog';
import {initials} from './SharingPage';
import {supabase} from '../lib/supabase';
import {holdAppointment,loadRequests,loadSlots} from '../lib/relationship';
import {proposeAction} from '../lib/actions';
import {AGENDA_PROFILE_FIELDS} from '../lib/agendaData';
import {consentCategoryText} from '../lib/consentData';
import {slotDay,slotTime} from '../lib/appointmentData';
import {displayName,firstName} from '../lib/format';
import {FORMATS,REQUEST_STEPS,decide,formatDay,loadHandoverDraft,loadMeetingOptions,meetingDays,noteLines,organiseAgenda,proposeIn,requestProgress,shareWithConsent,splitNote} from '../lib/relationshipWorkflow';

export const STEPS=[['request','Send a request'],['status','My requests'],['note','Handover note'],['meeting','Book a meeting']];
const URGENCY=[['low','Low'],['normal','Normal'],['high','High']];
async function token() {
  const {data,error}=await supabase.auth.getSession();
  if(error)throw error;
  if(!data.session)throw new Error('Please sign in again to continue with your relationship manager.');
  return data.session.access_token;
}
const first=firstName;
function Contact({advisor}) {
  return <dl className="rm-contact"><dt>Phone</dt><dd>{advisor.contact.phone||'Not recorded'}</dd><dt>Email</dt><dd>{advisor.contact.email||'Not recorded'}</dd></dl>;
}
function Fallback({intro,onSupport,children}) {
  return <div className="rm-fallback" data-testid="rm-fallback"><p>{children}</p>
    {intro?.current&&<p>You can contact {displayName(intro.current.name)} directly: {intro.current.contact.phone||'phone not recorded'}{intro.current.contact.email&&` · ${intro.current.contact.email}`}.</p>}
    <button className="bank-chat-logout" onClick={onSupport}><Icon name="support"/>Talk to a person</button></div>;
}

// Step 2 of Workflow 3: the bot introduces the new RM from the bank's records, with approved change wording only.
export function RelationshipIntro({intro,error,onStep,onAsk,onSupport,onSource,busy}) {
  if(error)return <div className="bank-chat-message assistant" data-testid="rm-intro"><div className="bank-chat-avatar"><Icon name="spark"/></div><div className="bank-chat-bubble">
    <div className="answer-heading">Banking assistant<small className="answer-origin">From your records</small></div>
    <Fallback onSupport={onSupport}>Your relationship manager's details could not be loaded right now ({error}).</Fallback></div></div>;
  const {current,previous,notice}=intro;
  return <div className="bank-chat-message assistant" data-testid="rm-intro"><div className="bank-chat-avatar"><Icon name="spark"/></div><div className="bank-chat-bubble">
    <div className="answer-heading">Banking assistant<small className="answer-origin">From your records</small></div>
    {!current?<Fallback onSupport={onSupport}>{notice[0]?.body||'No current relationship manager is recorded.'}</Fallback>:<>
      <p className="answer-text">{previous?<>You have a new relationship manager. {displayName(previous.name)} looked after you until {formatDay(previous.ended_on)}; from {formatDay(current.started_on)} it is <strong>{displayName(current.name)}</strong>.</>:<>Your relationship manager is <strong>{displayName(current.name)}</strong>.</>}</p>
      <article className="rm-card" data-testid="rm-card"><span className="initials large" aria-hidden="true">{initials(current.name)}</span>
        <div><h3 data-testid="rm-name">{displayName(current.name)}</h3><p>{current.role} · {current.team}</p><Contact advisor={current}/></div></article>
      {notice.length>0&&<div className="rm-notice" data-testid="rm-notice"><p><strong>{notice[0].heading}.</strong> {notice[0].body}</p>
        {notice.slice(1).map(item=><details key={item.anchor} className="rm-more"><summary>{item.heading}</summary><p>{item.body}</p></details>)}
        <button className="source-link" onClick={()=>onSource({id:'rm-change',title:notice[0].title,url:notice[0].url})}>Source: {notice[0].title}</button></div>}
      <div className="rm-chips" role="group" aria-label="Relationship manager options">
        {previous&&<button className="bank-chat-logout" data-testid="rm-ask-change" disabled={busy} onClick={()=>onAsk('Why did my relationship manager change, and will things stay the same?')}>Ask about the change</button>}
        {STEPS.map(([step,label])=><button key={step} className="bank-chat-logout" data-testid={`rm-open-${step}`} onClick={()=>onStep(step)}>{label==='Book a meeting'?`Meet ${first(current.name)}`:label}</button>)}
      </div></>}
  </div></div>;
}

function RequestStep({intro,question,result,setResult,onSent,onStatus,onSupport}) {
  const [what,setWhat]=useState(question||''),[urgency,setUrgency]=useState('normal'),[deadline,setDeadline]=useState('');
  const [proposal,setProposal]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  useEffect(()=>{if(question){setWhat(question);setResult(null);}},[question]);
  const name=intro.current.name;
  async function review(event) {
    event.preventDefault();setBusy(true);setError('');
    try {setProposal(await proposeIn('requests.create',{advisor_id:intro.current.rm_id,what,urgency,deadline:deadline||null,country:null,currency:null},await token()));}
    catch(failure) {setError(failure.message);}
    finally {setBusy(false);}
  }
  const stored=proposal?.command.target;
  return <section className="rm-step" data-testid="rm-step-request" aria-label="Send a request">
    <p className="rm-bot">I can send a request to {name}. What do you need, how urgent is it, and is there a deadline?</p>
    {result?<div className="rm-receipt" role="status" data-testid="rm-request-receipt"><Icon name="check"/><div>
      <p><strong>Received.</strong> Your request is in {result.receipt.result.recipient_name}'s request queue with status <strong>Sent</strong>.</p>
      <p>“{result.receipt.result.what}” · Urgency {result.receipt.result.urgency}{result.receipt.result.deadline&&` · Requested by ${formatDay(result.receipt.result.deadline)}`}</p>
      <p className="rm-small">Receipt <code>{result.receipt.receipt_id}</code></p>
      <div className="panel-actions"><button className="primary-button" data-testid="rm-request-view-status" onClick={onStatus}>Check its status below</button>
        <button className="bank-chat-logout" onClick={()=>{setResult(null);setWhat('');setDeadline('');setUrgency('normal');}}>Send another request</button></div></div></div>
    :<form className="rm-form" onSubmit={review}>
      <label className="form-field">What do you need?<textarea data-testid="rm-request-what" required maxLength={1000} rows={3} value={what} disabled={busy} onChange={event=>setWhat(event.target.value)} placeholder="For example: please send me my annual portfolio statement"/></label>
      <fieldset className="rm-urgency" disabled={busy}><legend>How urgent is it?</legend>{URGENCY.map(([value,label])=><label key={value}><input type="radio" name="rm-urgency" data-testid={`rm-urgency-${value}`} checked={urgency===value} onChange={()=>setUrgency(value)}/>{label}</label>)}</fieldset>
      <label className="form-field">Deadline (optional)<input type="date" data-testid="rm-request-deadline" value={deadline} disabled={busy} onChange={event=>setDeadline(event.target.value)}/></label>
      <p className="rm-small">Deadlines are requests, not bank promises. Never include passwords or OTPs.</p>
      <button className="primary-button" data-testid="rm-request-review" disabled={busy||!what.trim()}>Review request</button>
    </form>}
    {error&&<Fallback intro={intro} onSupport={onSupport}>{error}</Fallback>}
    {stored&&<RelationshipConfirmDialog label="Send request" title={`Send this request to ${stored.recipient_name}?`} confirmLabel="Send request" onClose={()=>setProposal(null)}
      onDecline={async()=>{await decide(proposal.command.command_id,false,await token());setProposal(null);}}
      onConfirm={async status=>{status('Sending your request…');const saved=await decide(proposal.command.command_id,true,await token());
        if(saved.receipt?.outcome!=='succeeded')throw new Error('Your relationship manager changed before sending, so nothing was sent. Reload and try again.');
        setProposal(null);setResult(saved);onSent?.();}}>
      <dl className="rm-review"><dt>To</dt><dd>{stored.recipient_name} (your current relationship manager)</dd><dt>Request</dt><dd data-testid="rm-review-what">{stored.what}</dd>
        <dt>Urgency</dt><dd>{stored.urgency}</dd><dt>Deadline</dt><dd>{stored.deadline?formatDay(stored.deadline):'None'}</dd></dl>
      <p className="rm-small">This is exactly what will be recorded. Private details were removed before saving.</p>
    </RelationshipConfirmDialog>}
  </section>;
}

function StatusStep({intro,refreshKey,onSupport,onRequest}) {
  const [requests,setRequests]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  async function load(){setBusy(true);setError('');try{setRequests(await loadRequests(await token()));}catch(failure){setError(failure.message);setRequests(null);}finally{setBusy(false);}}
  useEffect(()=>{load();},[refreshKey]);
  const names=useMemo(()=>({[intro.current.rm_id]:intro.current.name}),[intro]);
  return <section className="rm-step" data-testid="rm-step-status" aria-label="Your requests">
    <div className="rm-step-head"><p className="rm-bot">Here is the latest status of each request.</p><button className="bank-chat-logout" disabled={busy} onClick={load}>Refresh</button></div>
    {busy&&<p className="loading" role="status">Checking your requests…</p>}
    {error&&<Fallback intro={intro} onSupport={onSupport}>Request status is unavailable right now.</Fallback>}
    {requests&&!requests.data.length&&<p data-testid="rm-requests-empty">No requests are recorded. <button className="source-link" onClick={onRequest}>Send a request</button></p>}
    {requests&&<ol className="rm-requests" data-testid="rm-requests">{requests.data.map(item=>{
      const progress=requestProgress(item.status),current=item.advisor_id===intro.current.rm_id;
      return <li key={item.request_id} data-testid="rm-request" data-status={item.status}>
        <p className="rm-request-what">{item.what}</p>
        <ol className="rm-progress" aria-label={`Status: ${progress.label}`}>{REQUEST_STEPS.map((step,index)=><li key={step} className={index<progress.done?'done':''} aria-current={index===progress.done-1?'step':undefined}>{step[0].toUpperCase()+step.slice(1)}</li>)}</ol>
        {item.status==='closed'&&<p className="rm-small">Closed by the bank.</p>}
        <p data-testid="rm-request-action">{item.customer_action?<><strong>Your action:</strong> {item.customer_action}</>:'Nothing is needed from you right now.'}</p>
        <p className="rm-small">{current?`With ${names[item.advisor_id]}`:item.advisor_id&&intro.previous?`Raised with ${displayName(intro.previous.name)}, your previous relationship manager. It is not recorded as transferred.`:'Recipient not recorded.'}
          {item.deadline&&` · Requested by ${formatDay(item.deadline)}`} · Urgency {item.urgency}</p></li>;})}</ol>}
  </section>;
}

function NoteStep({intro,onSupport,onShared}) {
  const [lines,setLines]=useState(null),[fields,setFields]=useState([]),[topics,setTopics]=useState(''),[editing,setEditing]=useState(false);
  const [saved,setSaved]=useState(null),[sharing,setSharing]=useState(false),[days,setDays]=useState(30),[shared,setShared]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const agendaId=useRef(crypto.randomUUID()),revision=useRef(0);
  useEffect(()=>{(async()=>{setBusy(true);try{const draft=await loadHandoverDraft(await token());setLines(draft.topics);setFields(draft.profile_fields);}catch(failure){setError(failure.message);}finally{setBusy(false);}})();},[]);
  function addTopics(event) {
    event.preventDefault();const {drafted,agenda}=splitNote(lines);
    setLines(noteLines(drafted,[...agenda,...organiseAgenda(topics)]));setTopics('');setSaved(null);setShared(null);
  }
  async function save() {
    setBusy(true);setError('');
    try {
      const result=await proposeIn('agenda.save',{agenda_id:agendaId.current,advisor_id:intro.current.rm_id,expected_revision:revision.current,
        topics:lines.map(line=>line.trim()).filter(Boolean),profile_fields:fields},await token());
      if(result.receipt?.outcome!=='succeeded')throw new Error('The note could not be saved because your profile or relationship manager changed. Reload and try again.');
      revision.current=result.receipt.result.revision;setSaved(result);setEditing(false);return result;
    } catch(failure) {setError(failure.message);return null;}
    finally {setBusy(false);}
  }
  const valid=lines&&lines.length>=1&&lines.length<=12&&lines.every(line=>line.trim()&&Array.from(line).length<=300);
  const note=saved?.receipt.result,{agenda}=lines?splitNote(lines):{agenda:[]};
  return <section className="rm-step" data-testid="rm-step-note" aria-label="Handover note">
    <p className="rm-bot">I drafted a short note for {displayName(intro.current.name)} from your recorded profile and open requests. Add the topics you want to discuss, and edit anything before saving.</p>
    {busy&&!lines&&<p className="loading" role="status">Drafting your note from your records…</p>}
    {error&&<Fallback intro={intro} onSupport={onSupport}>{error}</Fallback>}
    {lines&&<>
      {editing?<label className="form-field">Edit the whole note (one line per item, up to 12)<textarea data-testid="rm-note-editor" rows={Math.min(12,lines.length+2)} value={lines.join('\n')} onChange={event=>{setLines(event.target.value.split('\n'));setSaved(null);setShared(null);}}/></label>
        :<ul className="rm-note" data-testid="rm-note-lines">{lines.map((line,index)=><li key={index}>{line}</li>)}</ul>}
      <fieldset className="rm-fields" disabled={busy||!!shared}><legend>Include from your profile</legend>{Object.entries(AGENDA_PROFILE_FIELDS).map(([field,label])=>
        <label key={field}><input type="checkbox" data-testid={`rm-note-field-${field}`} checked={fields.includes(field)} onChange={event=>{setSaved(null);setFields(current=>event.target.checked?[...current,field]:current.filter(value=>value!==field));}}/>{label}</label>)}</fieldset>
      <form className="rm-topic-form" onSubmit={addTopics}>
        <label className="form-field">Topics for your first meeting<input data-testid="rm-note-topics" value={topics} disabled={busy||!!shared} maxLength={1000} placeholder="e.g. estate planning, review of fees, how often we meet" onChange={event=>setTopics(event.target.value)}/></label>
        <button className="bank-chat-logout" data-testid="rm-note-add" disabled={busy||!!shared||!organiseAgenda(topics).length}>Add to agenda</button></form>
      {agenda.length>0&&<p className="rm-small" data-testid="rm-note-agenda">Agenda: {agenda.map((item,index)=>`${index+1}. ${item}`).join('  ')}</p>}
      <div className="panel-actions">
        <button className="bank-chat-logout" data-testid="rm-note-edit" disabled={busy||!!shared} onClick={()=>setEditing(!editing)}>{editing?'Done editing':'Edit whole note'}</button>
        <button className="bank-chat-logout" data-testid="rm-note-save" disabled={busy||!valid||!!shared||!!saved} onClick={save}>{saved?'Saved privately':'Save private draft'}</button>
        <button className="primary-button" data-testid="rm-note-share" disabled={busy||!valid||!!shared} onClick={async()=>{const result=saved||await save();if(result)setSharing(true);}}>Share with {first(intro.current.name)}…</button>
      </div>
      {saved&&!shared&&<p className="rm-small" data-testid="rm-note-saved">Saved privately (revision {note.revision}). Nothing has been shared. Receipt <code>{saved.receipt.receipt_id}</code></p>}
      {shared&&<div className="rm-receipt" role="status" data-testid="rm-note-shared"><Icon name="check"/><div><p><strong>Shared.</strong> {shared.shared.receipt.result.recipient_name} can now see this version of your note.</p>
        <p className="rm-small">Consent {shared.grant.expires_at?`lasts until ${formatDay(shared.grant.expires_at)}`:'has no expiry'}. Later edits are not sent. Receipt <code>{shared.shared.receipt.receipt_id}</code></p></div></div>}
    </>}
    {sharing&&note&&<RelationshipConfirmDialog label="Share handover note" title={`Share your note with ${note.recipient_name}?`} subtitle="Your consent is needed before anything is shared." confirmLabel="Give consent and share"
      acknowledgement={`I consent to DemoBank sharing this note with ${note.recipient_name} for a relationship-manager briefing.`}
      onClose={()=>setSharing(false)} onDecline={async()=>{setSharing(false);setError('');}} declineLabel="Don't share"
      onConfirm={async status=>{const result=await shareWithConsent({agenda:{...note,agenda_id:note.agenda_id},durationDays:days,token:await token(),
        onStep:step=>status({consent_request:'Recording your consent…',consent_grant:'Confirming your consent…',share:'Sharing the note…'}[step])});setSharing(false);setShared(result);onShared?.();}}>
      <dl className="rm-review"><dt>Recipient</dt><dd data-testid="rm-share-recipient">{note.recipient_name}, {intro.current.role}</dd>
        <dt>How it will be used</dt><dd>{first(note.recipient_name)} reads it to prepare for your first meeting. DemoBank keeps this exact version as a shared copy.</dd>
        <dt>Data included</dt><dd>{consentCategoryText(note.categories)}</dd>
        <dt>Consent lasts</dt><dd><select data-testid="rm-share-duration" value={days} onChange={event=>setDays(Number(event.target.value))}>{[7,30,90].map(value=><option key={value} value={value}>{value} days</option>)}</select></dd></dl>
      <pre className="agenda-content" data-testid="rm-share-preview">{note.content}</pre>
      <p className="rm-small">You can revoke consent later in Data sharing. Revoking stops future sharing but does not delete a copy already shared.</p>
    </RelationshipConfirmDialog>}
  </section>;
}

function MeetingStep({intro,question,onSupport,onBooked}) {
  const days=useMemo(()=>meetingDays(question),[question]);
  const [slots,setSlots]=useState(null),[options,setOptions]=useState({}),[format,setFormat]=useState('video'),[hold,setHold]=useState(null),[proposal,setProposal]=useState(null);
  const [booked,setBooked]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[now,setNow]=useState(Date.now());
  async function load() {
    setBusy(true);setError('');
    try {
      const access=await token();
      const [found,meeting]=await Promise.all([Promise.all(days.map(date=>loadSlots(access,{from_date:date,to_date:date,limit:6}))),loadMeetingOptions(access)]);
      setSlots(found.flatMap(result=>result.data));setOptions(meeting.options);
    } catch(failure) {setError(failure.message);setSlots(null);}
    finally {setBusy(false);}
  }
  useEffect(()=>{setHold(null);setBooked(null);load();},[days.join()]);
  useEffect(()=>{if(!hold)return;const timer=setInterval(()=>setNow(Date.now()),1000);return ()=>clearInterval(timer);},[hold]);
  async function pick(slot) {
    setBusy(true);setError('');
    try {
      const saved=await holdAppointment({command_id:crypto.randomUUID(),session_id:crypto.randomUUID(),case_id:crypto.randomUUID(),target:{...slot,format}},await token());
      if(saved.receipt?.outcome!=='succeeded'){setError('That time was just taken. Please choose another.');await load();return;}
      setHold(saved);setNow(Date.now());
    } catch(failure) {setError(failure.message);}
    finally {setBusy(false);}
  }
  async function review() {
    setBusy(true);setError('');
    try {setProposal(await proposeAction({command_id:crypto.randomUUID(),case_id:hold.command.case_id,task_id:crypto.randomUUID(),type:'appointment.confirm',
      target:{appointment_id:hold.receipt.result.appointment_id}},await token()));}
    catch(failure) {setError(failure.message);}
    finally {setBusy(false);}
  }
  const held=hold?.receipt.result,left=held?Math.max(0,Date.parse(held.hold_expires_at)-now):0;
  const byDay=useMemo(()=>{const groups=new Map();for(const slot of slots||[]){const key=slotDay(slot.starts_at);groups.set(key,[...(groups.get(key)||[]),slot]);}return [...groups];},[slots]);
  const details=item=>options[item.format]?.joining_details||'Joining details are not recorded for this format. Your relationship manager will confirm them.';
  return <section className="rm-step" data-testid="rm-step-meeting" aria-label="Book a meeting">
    <p className="rm-bot">Here are {first(intro.current.name)}'s available times{days.length?` from ${slotDay(`${days[0]}T12:00:00+08:00`)} to ${slotDay(`${days.at(-1)}T12:00:00+08:00`)}`:''} (Singapore time). Pick one and I'll hold it for 10 minutes.</p>
    {booked?<div className="rm-receipt" role="status" data-testid="rm-booking-confirmed"><Icon name="check"/><div>
      <p><strong>Booked.</strong> {slotDay(booked.starts_at)}, {slotTime(booked.starts_at)}–{slotTime(booked.ends_at)} (Singapore time) with {displayName(intro.current.name)}.</p>
      <p>Format: {FORMATS[booked.format]}</p><p data-testid="rm-booking-joining">Joining details: {details(booked)}</p>
      <p className="rm-small">Receipt <code>{booked.receipt_id}</code>. This is a coursework demo: no real meeting or calendar invite is created.</p></div></div>
    :hold?<div className="rm-hold" data-testid="rm-hold">
      <p><strong>Held for you:</strong> {slotDay(held.starts_at)}, {slotTime(held.starts_at)}–{slotTime(held.ends_at)} · {FORMATS[held.format]}</p>
      <p data-testid="rm-hold-timer">{left>0?`This time is held for ${Math.floor(left/60000)}:${String(Math.floor(left/1000)%60).padStart(2,'0')} more minutes. It is not booked until you confirm.`:'The hold has expired. Choose a time again.'}</p>
      <div className="panel-actions"><button className="primary-button" data-testid="rm-booking-review" disabled={busy||left<=0} onClick={review}>Confirm booking…</button>
        <button className="bank-chat-logout" disabled={busy} onClick={()=>{setHold(null);load();}}>Choose another time</button></div></div>
    :<>
      <label className="form-field rm-format">Meeting format<select data-testid="rm-meeting-format" value={format} disabled={busy} onChange={event=>setFormat(event.target.value)}>{Object.entries(FORMATS).map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>
      {busy&&!slots&&<p role="status">Checking {first(intro.current.name)}'s calendar…</p>}
      {slots&&!slots.length&&<Fallback intro={intro} onSupport={onSupport}>No times are available on those days.</Fallback>}
      {byDay.map(([label,items])=><div key={label} className="rm-day"><h4>{label}</h4><div className="rm-slots">{items.map(slot=>
        <button key={slot.starts_at} className="bank-chat-logout" data-testid="rm-slot" disabled={busy} onClick={()=>pick(slot)}><time dateTime={slot.starts_at}>{slotTime(slot.starts_at)}</time></button>)}</div></div>)}
    </>}
    {error&&<Fallback intro={intro} onSupport={onSupport}>{error} Direct booking is unavailable right now.</Fallback>}
    {proposal&&<RelationshipConfirmDialog label="Confirm booking" title="Book this meeting?" confirmLabel="Book meeting" declineLabel="Not now" onClose={()=>setProposal(null)}
      onDecline={async()=>{await decide(proposal.command.command_id,false,await token());setProposal(null);}}
      onConfirm={async status=>{status('Booking with the scheduling system…');const saved=await decide(proposal.command.command_id,true,await token());
        if(saved.receipt?.outcome!=='succeeded')throw new Error('The time is no longer available or the hold expired. Nothing was booked. Choose another time.');
        setProposal(null);setHold(null);setBooked({...saved.receipt.result,receipt_id:saved.receipt.receipt_id});onBooked?.();}}>
      <dl className="rm-review"><dt>With</dt><dd>{displayName(intro.current.name)}</dd><dt>When</dt><dd data-testid="rm-booking-when">{slotDay(held.starts_at)}, {slotTime(held.starts_at)}–{slotTime(held.ends_at)} (Singapore time)</dd>
        <dt>Format</dt><dd>{FORMATS[held.format]}</dd><dt>Joining details</dt><dd>{details(held)}</dd></dl>
    </RelationshipConfirmDialog>}
  </section>;
}

// The steps after the introduction, shown in the conversation. The active step follows the customer's latest message.
function MeetStep({intro,onAsk,onSource}) {
  const {current,previous,notice}=intro;
  return <section className="rm-step" data-testid="rm-step-meet" aria-label="Meet your relationship manager">
    <p className="rm-bot">{previous?`${displayName(previous.name)} looked after you until ${formatDay(previous.ended_on)}. From ${formatDay(current.started_on)}, ${displayName(current.name)} is your relationship manager.`:`${displayName(current.name)} is your relationship manager.`}</p>
    <article className="rm-card"><span className="initials large" aria-hidden="true">{initials(current.name)}</span>
      <div><h3>{displayName(current.name)}</h3><p>{current.role} · {current.team}</p><Contact advisor={current}/></div></article>
    {notice.length>0&&<div className="rm-notice">{notice.map(item=><p key={item.anchor}><strong>{item.heading}.</strong> {item.body}</p>)}
      <button className="source-link" onClick={()=>onSource?.({id:'rm-change',title:notice[0].title,url:notice[0].url})}>Source: {notice[0].title}</button></div>}
    {previous&&onAsk&&<div className="panel-actions"><button className="bank-chat-logout" data-testid="rm-meet-ask" onClick={()=>onAsk('Why did my relationship manager change, and will things stay the same?')}>Ask the assistant about the change</button></div>}
  </section>;
}

export const FLOW=[['meet','Meet your RM','Who looks after you now'],['request','Send a request','And check its status'],['note','Handover note','What matters to you'],['meeting','Book a meeting','Your first conversation']];

// Workflow 3 as a guided checklist. A step shows ✔ only once its server receipt exists; steps stay mounted after a visit so nothing is lost.
export default function RelationshipWorkspace({intro,focus,onFocus,onSupport,onClose,onAsk,onSource,embedded=false}) {
  const [refresh,setRefresh]=useState(0),[sent,setSent]=useState(null),[done,setDone]=useState({meet:true}),[visited,setVisited]=useState(()=>new Set());
  const status=useRef(null);
  const raw=focus?.step||'meet',step=raw==='status'?'request':raw;
  useEffect(()=>{setVisited(current=>current.has(step)?current:new Set(current).add(step));},[step]);
  useEffect(()=>{if(raw==='status')requestAnimationFrame(()=>status.current?.scrollIntoView({behavior:'smooth',block:'nearest'}));},[focus?.key]);
  if(!intro?.current)return null;
  const mark=key=>setDone(current=>({...current,[key]:true}));
  const go=key=>onFocus({step:key,question:null,key:Date.now()});
  const index=FLOW.findIndex(([key])=>key===step),next=FLOW[index+1];
  const shown=key=>visited.has(key)||step===key;
  return <section className={`rm-workspace ${embedded?'rm-embedded':'compliance-workspace'}`} data-testid="rm-workspace" aria-label="Your relationship manager">
    {!embedded&&<div className="panel-heading"><div className="rm-head"><span className="initials" aria-hidden="true">{initials(intro.current.name)}</span>
      <div><span className="eyebrow">Your new relationship manager</span><h3>{displayName(intro.current.name)}</h3></div></div>
      <button className="icon-button" aria-label="Close relationship panel" onClick={onClose}><Icon name="close"/></button></div>}
    <div className="rm-flow">
      <ol className="rm-checklist" aria-label="Steps with your relationship manager">{FLOW.map(([key,label,hint],i)=><li key={key}>
        <button data-testid={`rm-nav-${key}`} aria-current={step===key?'step':undefined} className={`${step===key?'current':''} ${done[key]?'done':''}`} onClick={()=>go(key)}>
          <span className="rm-check" aria-hidden="true">{done[key]?<Icon name="check"/>:i+1}</span>
          <span className="rm-check-text"><strong>{label}</strong><small>{done[key]?'Done':step===key?hint:'To do'}</small></span></button></li>)}</ol>
      <div className="rm-stage">
        <p className="rm-stage-title">Step {index+1} of {FLOW.length} · {FLOW[index][1]}</p>
        {shown('meet')&&<div hidden={step!=='meet'}><MeetStep intro={intro} onAsk={onAsk} onSource={onSource}/></div>}
        {shown('request')&&<div hidden={step!=='request'}>
          <RequestStep intro={intro} question={focus?.step==='request'?focus.question:null} result={sent} setResult={setSent} onSupport={onSupport}
            onSent={()=>{setRefresh(value=>value+1);mark('request');}} onStatus={()=>{setRefresh(value=>value+1);status.current?.scrollIntoView({behavior:'smooth',block:'nearest'});}}/>
          <div ref={status} className="rm-status-block"><h4>Your requests</h4><StatusStep intro={intro} refreshKey={`${refresh}:${raw==='status'?focus?.key:''}`} onSupport={onSupport} onRequest={()=>go('request')}/></div>
        </div>}
        {shown('note')&&<div hidden={step!=='note'}><NoteStep intro={intro} onSupport={onSupport} onShared={()=>mark('note')}/></div>}
        {shown('meeting')&&<div hidden={step!=='meeting'}><MeetingStep key={focus?.step==='meeting'&&focus.question?focus.question:'meeting'} intro={intro} question={focus?.step==='meeting'?focus.question:null} onSupport={onSupport} onBooked={()=>mark('meeting')}/></div>}
        <div className="rm-next">{next?<button className="primary-button" data-testid="rm-next" onClick={()=>go(next[0])}>Next: {next[1]} →</button>
          :<span className="rm-small">{done.meeting?'All set. Your new relationship manager has what they need.':'Book a time to finish.'}</span>}</div>
      </div>
    </div>
    {!embedded&&<p className="rm-small rm-footnote">Coursework demo with synthetic records. Each step is recorded only after you confirm it; no real advisor is contacted.</p>}
  </section>;
}
