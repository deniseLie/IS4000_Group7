import {useEffect,useRef,useState} from 'react';
import Icon from '../Icon';
import {FORMAT_WORDS,awarenessList,display,meetingTitle,openMeetingPage,todayAction,openSchedule,prepareMissingBriefs,ticketRows,requestsToAnswer,rmGet,todayList,todayView} from '../../lib/rm';
import {meetingRequest} from '../../lib/meetingLifecycle';
import {sharedTopicRows} from '../../lib/talkingPoints';
import {formatDate} from '../../lib/format';

const SHOWN=5;
const KIND_WORDS={specialist_ticket:'With a bank team',commitment:'Recorded commitment',review_deadline:'Compliance review',milestone:'Date on record',task:'Your task'};
function timeOf(when) {return (when||'').split(', ').at(-1);}
function daysAway(date,day) {const n=Math.round((Date.parse(date)-Date.parse(day))/86400000);return n===0?'today':n===1?'tomorrow':`in ${n} days`;}

// The RM's start page (Today redesign): her next meeting, then ONE ranked list mixing requests, meetings to document,
// dates and topics clients shared, each with one sentence and one button. "See all" opens the whole list on this page
// (it replaces the old Follow-ups page). Read straight from her records: no model call, nothing sent or changed here.
// The order and reasons come from GET /rm/priorities (services/rm/priority.py); the page never scores.
export default function TodayPage({token,onAsk,advisor,today}) {
  const [data,setData]=useState(null),[error,setError]=useState(''),[all,setAll]=useState(false);
  const [prepared,setPrepared]=useState(0);
  // Briefing opens the assistant and writes the brief there (one place for every generated answer), not under the button.
  const brief=a=>onAsk(`Brief me for my meeting with ${a.client}`,{intent:'meeting_brief',context:{client_id:a.customer_id,appointment_id:a.appointment_id}});
  const preparation=useRef(null);
  async function load() {
    setError('');
    try {
      const auth=await token();
      const optional=promise=>promise.catch(()=>null);  // shared topics and meeting reminders are extras: never block Today
      const [a,t,f,m,p,s,r]=await Promise.all([rmGet('/appointments?past_days=30',auth),rmGet('/tickets',auth),rmGet('/followups',auth),rmGet('/meeting-requests',auth),
        rmGet('/priorities',auth),optional(rmGet('/shared-topics',auth)),
        optional(meetingRequest('/capabilities','GET',null,auth).then(c=>c.enabled?meetingRequest('/reminders','GET',null,auth):[]))]);
      setData({appointments:a.data,tickets:t.data,followups:f.data,meetingRequests:requestsToAnswer(m.data),priorities:p.items||[],shared:sharedTopicRows(s?.data),reminders:r||[]});
    } catch(failure) {setError(failure.message);}
  }
  useEffect(()=>{load();if(!preparation.current)preparation.current=prepareMissingBriefs(token,()=>setPrepared(n=>n+1))
    .catch(failure=>setError(`${failure.message} Open that meeting’s briefing to retry.`));},[]);  // eslint-disable-line react-hooks/exhaustive-deps
  const day=today||new Date().toISOString().slice(0,10);
  const v=data?todayView(data,day):null;
  const next=data?data.appointments.filter(a=>a.status==='confirmed'&&!a.past).sort((x,y)=>(x.starts_at||x.date).localeCompare(y.starts_at||y.date))[0]:null;
  const list=data?todayList({priorities:data.priorities,reminders:data.reminders,toDocument:v.toDocument,shared:data.shared,tickets:data.tickets?.rm_owned}).filter(x=>!next||x.meeting!==next.appointment_id||x.doc):[];  // the next meeting is the card on top
  const awareness=data?awarenessList(data.followups,data.priorities):[];
  const first=advisor?display(advisor.name).split(' ')[0]:'';
  const hour=Number(new Intl.DateTimeFormat('en-SG',{hour:'numeric',hour12:false,timeZone:'Asia/Singapore'}).format(new Date()));
  const greeting=hour<12?'Good morning':hour<18?'Good afternoon':'Good evening';
  const toDocumentCount=list.filter(x=>x.doc).length;
  const dates=v?v.soon.length+v.overdueTasks.length:0;
  function act(x) {
    const a=todayAction(x);
    return {label:a.label,run:()=>a.meeting?openMeetingPage(a.meeting,x.client):a.schedule?openSchedule(a.schedule):onAsk(a.message,{intent:a.intent,context:a.context})};
  }
  const shown=all?list:list.slice(0,SHOWN);
  return <div className="app-page-body rmw-page rmw-today" data-testid="rm-today">
    <header className="page-header"><div><span className="eyebrow">{formatDate(day,{weekday:'long'})}</span><h1>{greeting}{first?`, ${first}`:''}</h1>
      <p data-testid="rm-today-count">{!data?'Reading your records…':list.length?`${list.length} thing${list.length===1?' needs':'s need'} you today. Start at the top.`:'Nothing needs you right now.'}</p></div>
      <div className="page-header-actions"><button className="bank-chat-logout" data-testid="rm-today-plan" onClick={()=>onAsk('What’s on today?',{intent:'today'})}><Icon name="spark"/>Ask the assistant</button>
        <button className="icon-button" aria-label="Refresh" title="Refresh" onClick={load}><Icon name="refresh"/></button></div></header>
    {error&&<p role="alert" className="rm-error">{error}</p>}
    {!data&&!error&&<div className="loading-list" role="status" aria-label="Loading">{[0,1,2].map(k=><span key={k} className="skeleton-card"/>)}</div>}
    {data&&<>
      {next&&<section className="rmw-card rm-nextmeet" data-testid="rm-today-next" aria-label="Next meeting">
        <div><span className="eyebrow">Next meeting · {next.when} · {daysAway(next.date,day)}</span>
          <strong>{next.client} · {meetingTitle(next.purpose,next.topics)}</strong>
          <small className="rmw-why">{FORMAT_WORDS[next.format]||'Meeting'}{next.format==='video'?' call':''}{prepared>0?' · briefing prepared':''}</small></div>
        <div className="rm-nextmeet-actions"><button className="bank-chat-logout" data-testid="rm-today-brief" onClick={()=>brief(next)}>Briefing</button>
          <button className="primary-button" data-testid="rm-today-open-meeting" onClick={()=>openMeetingPage(next.appointment_id,next.client)}>Open meeting</button></div>
      </section>}

      <div className="rm-today-grid">
        <section className="rmw-card rm-first" data-testid="rm-today-first" aria-label={all?'Everything that needs you':'Do first'}>
          <div className="rm-first-head"><h2>{all?'Everything that needs you':'Do first'}</h2></div>
          {!list.length&&<p className="rm-none">Nothing waiting for you. New requests, meetings and dates will appear here.</p>}
          {shown.map(x=>{const a=act(x);return <div key={x.key} className="rm-now-row" data-testid="rm-today-item">
            <span className={`rm-dot ${x.tone}`} aria-hidden="true"/>
            <span className="rm-now-text"><span className="rm-type">{x.label}</span><strong>{x.client}</strong> · {x.title}{x.why&&<small className="rmw-why">{x.why}</small>}</span>
            <button className="bank-chat-logout" onClick={a.run}>{a.label}</button></div>;})}
          {list.length>SHOWN&&<button className="rm-plain rm-see-all" data-testid="rm-today-see-all" aria-expanded={all} onClick={()=>setAll(o=>!o)}>
            {all?'Show only the top 5':`See all ${list.length} things that need you →`}</button>}
          {all&&awareness.length>0&&<div className="rm-aware" data-testid="rm-today-awareness"><h3>For awareness</h3><p className="rm-small">On record but nothing for you to do yet: bank teams’ cases, commitments and later dates.</p>
            {awareness.map(f=><div key={f.item_key} className="rm-now-row"><span className="rm-dot" aria-hidden="true"/>
              <span className="rm-now-text"><span className="rm-type">{KIND_WORDS[f.kind]||'On record'}</span><strong>{f.client}</strong> · {String(f.title||'').replace(/^Your task: /,'')}
                {f.due_text&&<small className="rmw-why">{f.overdue?'Was due':'Due'} {f.due_text}</small>}</span></div>)}</div>}
        </section>

        <aside className="rm-today-side">
          <section className="rmw-card" aria-label="This week"><h3>This week</h3>
            {!v.upcoming.length?<p className="rm-none">No confirmed meetings in the next 7 days.</p>:
              v.upcoming.slice(0,4).map(a=><div key={a.appointment_id} className="rm-week-row"><span className="rm-week-day">{a.date===day?'Today':formatDate(a.date,{weekday:'short',year:undefined,month:undefined})}<small>{timeOf(a.when)}</small></span>
                <button className="rm-plain" onClick={()=>openMeetingPage(a.appointment_id,a.client)}>{a.client}<small>{meetingTitle(a.purpose,a.topics)}</small></button></div>)}
            <button className="rm-plain rm-see-all" onClick={()=>openSchedule('appointments')}>Open Schedule →</button></section>
          <section className="rm-counts" aria-label="Everything else">
            <Count n={ticketRows(v.requests).length} label="client requests" onClick={()=>{location.hash='rm-tickets';}}/>
            <Count n={toDocumentCount} label="meetings to document" onClick={()=>openSchedule('appointments')}/>
            <Count n={dates} label="dates coming up" onClick={()=>setAll(true)}/>
            <Count n={data.meetingRequests.length} label={`meeting request${data.meetingRequests.length===1?'':'s'} to answer`} onClick={()=>openSchedule('requests')}/>
          </section>
          <p className="rm-small">Client requests are also in Ticket inbox; meetings in Schedule.</p>
        </aside>
      </div>
    </>}
  </div>;
}

function Count({n,label,onClick}) {
  return <button type="button" className={`rm-count ${n?'':'zero'}`} onClick={onClick}><strong>{n}</strong><span>{label}</span></button>;
}
