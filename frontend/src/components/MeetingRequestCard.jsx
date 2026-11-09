import {useState} from 'react';
import Icon from './Icon';
import {supabase} from '../lib/supabase';
import {loadSlots} from '../lib/relationship';
import {confirmAction,createActionCase,proposeAction} from '../lib/actions';
import {FORMATS} from '../lib/relationshipWorkflow';
import {LIMITS,TOPICS,meetingRequestTarget,slotLabel,toggleTime,toggleTopic} from '../lib/meetingRequests';
import {EXPIRED_LINE,expiresIn} from '../lib/offers';

// The meeting request the assistant drafted, as a small form in the chat (design: "drafted card → Edit in place → Send →
// Sent"). Send as drafted is her Confirm on the offer (the same as typing yes). Edit opens the fields in place; sending an
// edited request is her own click on the same meeting.request action as the Meetings page (propose, then her click
// confirms it). Nothing is sent before she presses Send.
const LEAD=2*60*60*1000;
async function token() {
  const {data,error}=await supabase.auth.getSession();
  if(error)throw error;
  if(!data.session)throw new Error('Please sign in again to send this request.');
  return data.session.access_token;
}
function range(days=14) {
  const now=new Date(Date.now()+8*60*60*1000);
  return {from_date:now.toISOString().slice(0,10),to_date:new Date(now.getTime()+(days-1)*86400000).toISOString().slice(0,10),limit:20};
}

export default function MeetingRequestCard({draft,offer,state,busy,onDecide,onSent,onAsk,onNavigate,onMeetings}) {
  const [mode,setMode]=useState('view'),[slots,setSlots]=useState(null),[error,setError]=useState(''),[working,setWorking]=useState(false),[sent,setSent]=useState(null);
  const [purpose,setPurpose]=useState(draft.purpose),[topics,setTopics]=useState(draft.topics.length?draft.topics:['other']),[format,setFormat]=useState(draft.format),[times,setTimes]=useState([]);
  const first=draft.rm.split(' ')[0];
  const expired=state==='expired',answered=state==='answered'||!!sent;
  async function edit() {
    setMode('edit');setError('');
    if(slots)return;
    try {
      const free=(await loadSlots(await token(),range())).data.filter(slot=>Date.parse(slot.starts_at)>Date.now()+LEAD);
      setSlots(free);setTimes(free.filter(slot=>draft.times.includes(slotLabel(slot.starts_at))).map(slot=>slot.starts_at).slice(0,LIMITS.times));
    } catch(failure) {setError(failure.message);}
  }
  async function sendEdited() {
    setError('');let target;
    try {target=meetingRequestTarget({advisorId:slots?.[0]?.advisor_id,purpose:purpose.trim()||topics.map(t=>TOPICS[t]).join(', '),topics,format,times});}
    catch(failure) {setError(failure.message);return;}
    setWorking(true);
    try {
      const access=await token();
      const scope=await createActionCase({session_id:crypto.randomUUID(),case_id:crypto.randomUUID()},access);
      const proposed=await proposeAction({command_id:crypto.randomUUID(),case_id:scope.case_id,task_id:crypto.randomUUID(),type:'meeting.request',target},access);
      const result=await confirmAction(proposed.command.command_id,access);
      if(result.receipt?.outcome!=='succeeded')throw new Error('That changed before it could be sent. Check Meetings and try again.');
      setSent({purpose:target.purpose,topics:target.topics,format:target.format,times:target.preferred_times.map(slotLabel),by:result.receipt.result?.respond_by_text});
      setMode('view');onSent?.();
    } catch(failure) {setError(failure.message);} finally {setWorking(false);}
  }
  const shown=sent||{purpose:draft.purpose,topics:draft.topics,format:draft.format,times:draft.times};
  const rows=<>
    <div className="mrc-row"><b>About</b><span>{shown.purpose}</span></div>
    <div className="mrc-row"><b>Topics</b><span className="mrc-chips">{shown.topics.map(t=><span key={t} className="mrc-chip on">{TOPICS[t]}</span>)}</span></div>
    <div className="mrc-row"><b>How</b><span>{FORMATS[shown.format]}</span></div>
    <div className="mrc-row"><b>Times</b><span>{shown.times.map(t=><span key={t} className="mrc-time">{t}</span>)}</span></div></>;

  if(sent)return <div className="mrc card-like" data-testid="meeting-request-card" data-state="sent">
    <div className="mrc-ok" role="status"><Icon name="check"/><span><strong>Sent to {draft.rm}</strong><br/>{first} usually replies within one business day{sent.by?` (by ${sent.by})`:''}. You’ll get a note in your Inbox.</span></div>
    {rows}
    <div className="mrc-next"><b>Next:</b>
      <button type="button" className="mrc-pill main" data-testid="meeting-handoff" onClick={()=>(onMeetings||(()=>onNavigate?.('meetings')))()}>Open Meetings</button>
      <button type="button" className="mrc-pill" onClick={()=>onAsk?.('What should I prepare for my meeting with my relationship manager?')}>What should I prepare?</button></div>
  </div>;

  if(mode==='edit')return <div className="mrc card-like editing" data-testid="meeting-request-card" data-state="editing">
    <span className="eyebrow">Meeting request · editing</span>
    <div className="mrc-row"><b>About</b><textarea data-testid="mrc-purpose" maxLength={LIMITS.purpose} rows={2} value={purpose} onChange={e=>setPurpose(e.target.value)}/></div>
    <div className="mrc-row"><b>Topics</b><span className="mrc-chips">{Object.entries(TOPICS).map(([code,label])=><button type="button" key={code} aria-pressed={topics.includes(code)}
      className={`mrc-chip ${topics.includes(code)?'on':''}`} onClick={()=>setTopics(toggleTopic(topics,code))}>{label}</button>)}</span></div>
    <div className="mrc-row"><b>How</b><span className="mrc-chips">{Object.entries(FORMATS).map(([code,label])=><button type="button" key={code} aria-pressed={format===code}
      className={`mrc-chip ${format===code?'on':''}`} onClick={()=>setFormat(code)}>{label}</button>)}</span></div>
    <div className="mrc-row"><b>Times</b><span className="mrc-chips">{!slots&&!error&&<span className="rm-small">Loading free times…</span>}
      {slots&&!slots.length&&<span className="rm-small">No free times in the next 14 days. Use Talk to a person to ask for another time.</span>}
      {slots?.slice(0,12).map(slot=>{const on=times.includes(slot.starts_at);return <button type="button" key={slot.starts_at} aria-pressed={on} data-testid="mrc-slot"
        className={`mrc-chip ${on?'on':''}`} disabled={!on&&times.length>=LIMITS.times} onClick={()=>setTimes(toggleTime(times,slot.starts_at))}>{slotLabel(slot.starts_at)}</button>;})}</span></div>
    <p className="rm-small">Pick up to {LIMITS.times} times (Singapore time). You can also just type a change, for example “Thursday afternoon”.</p>
    {error&&<p role="alert" className="rm-error">{error}</p>}
    <div className="mrc-actions"><button type="button" className="primary-button" data-testid="mrc-send-edited" disabled={working||!slots||!times.length||!topics.length} onClick={sendEdited}>{working?'Sending…':`Send to ${first}`}</button>
      <button type="button" className="bank-chat-logout" disabled={working} onClick={()=>setMode('view')}>Back</button></div>
  </div>;

  return <div className="mrc card-like" data-testid="meeting-request-card" data-state={answered?'answered':expired?'expired':'open'}>
    <span className="eyebrow">Meeting request · to {draft.rm}</span>
    <div className="mrc-title">{draft.topics.map(t=>TOPICS[t]).join(' · ')||'Meeting request'}</div>
    {!answered&&<div className="mrc-one">{draft.note?`${draft.note} `:''}I’ve filled this in from what you said. Check it, change anything, then send.</div>}
    {rows}
    {!answered&&<>
      {error&&<p role="alert" className="rm-error">{error}</p>}
      <div className="mrc-actions">
        <button type="button" className="primary-button" data-testid="offer-confirm" disabled={expired||busy} onClick={()=>onDecide('confirm',{})}>Send to {first}</button>
        <button type="button" className="bank-chat-logout" data-testid="mrc-edit" disabled={expired||busy} onClick={edit}>Edit</button>
        <button type="button" className="bank-chat-logout" data-testid="offer-cancel" disabled={expired||busy} onClick={()=>onDecide('cancel',{})}>Cancel</button></div>
      <p className="rm-small" data-testid="offer-expiry">{expired?EXPIRED_LINE:`Nothing is sent until you press Send · ${expiresIn(offer).toLowerCase()}`}</p></>}
    {/* One quiet way to the Meetings page inside the card, instead of a second red button under it. */}
    <div className="mrc-foot"><button type="button" className="link-button" data-testid="meeting-handoff" onClick={()=>(onMeetings||(()=>onNavigate?.('meetings')))()}>
      {answered?'See it on the Meetings page':'Or set it up on the Meetings page'}<Icon name="arrow"/></button></div>
  </div>;
}
