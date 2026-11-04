import {useEffect,useState} from 'react';
import Icon from '../Icon';
import {FORMAT_WORDS,display,openSchedule,receivedText,requestsToAnswer,rmGet,todayView,topicLabel} from '../../lib/rm';
import {formatDate} from '../../lib/format';

const BOOK_QUESTIONS=['Which clients have a maturity or review in the next 30 days?','Who haven’t I met in the last 90 days?','Which clients have requests waiting for me?'];
function timeOf(when) {return (when||'').split(', ').at(-1);}

// The RM's start page: what needs her now, read straight from her records (no model call, nothing sent). Every item has
// one obvious next step that opens the assistant with the right client, meeting or request already selected. Meeting
// requests to answer (overdue first, new ones marked; 3b ranks them) open Schedule → Requests, where she decides.
export default function TodayPage({token,onAsk,advisor,today}) {
  const [data,setData]=useState(null),[error,setError]=useState(''),[question,setQuestion]=useState('');
  async function load() {
    setError('');
    try {
      const auth=await token();
      const [a,t,f,m]=await Promise.all([rmGet('/appointments?past_days=30',auth),rmGet('/tickets',auth),rmGet('/followups',auth),rmGet('/meeting-requests',auth)]);
      setData({appointments:a.data,tickets:t.data,followups:f.data,meetingRequests:requestsToAnswer(m.data)});
    } catch(failure) {setError(failure.message);}
  }
  useEffect(()=>{load();},[]);  // eslint-disable-line react-hooks/exhaustive-deps
  const day=today||new Date().toISOString().slice(0,10);
  const v=data?todayView(data,day):null;
  const first=advisor?display(advisor.name).split(' ')[0]:'';
  const hour=Number(new Intl.DateTimeFormat('en-SG',{hour:'numeric',hour12:false,timeZone:'Asia/Singapore'}).format(new Date()));
  const greeting=hour<12?'Good morning':hour<18?'Good afternoon':'Good evening';
  const brief=a=>onAsk(`Brief me for my meeting with ${a.client} on ${a.when}`,{intent:'meeting_brief',context:{client_id:a.customer_id,appointment_id:a.appointment_id}});
  const documentMeeting=a=>onAsk(`Notes from my ${FORMAT_WORDS[a.format].toLowerCase()} meeting with ${a.client} on ${a.when}: `,{context:{client_id:a.customer_id,appointment_id:a.appointment_id},prefill:true});
  const ask=text=>text.trim()&&onAsk(text.trim(),{intent:'book_question'});
  return <div className="app-page-body rmw-page rmw-today" data-testid="rm-today">
    <header className="page-header"><div><span className="eyebrow">{formatDate(day,{weekday:'long'})}</span><h1>{greeting}{first?`, ${first}`:''}</h1>
      <p>What needs you today, straight from your records. Nothing here is sent or changed until you act.</p></div>
      <div className="page-header-actions"><button className="bank-chat-logout" onClick={load}><Icon name="refresh"/>Refresh</button></div></header>
    {error&&<p role="alert" className="rm-error">{error}</p>}
    {!v&&!error&&<div className="loading-list" role="status" aria-label="Loading">{[0,1,2].map(k=><span key={k} className="skeleton-card"/>)}</div>}
    {v&&<>
      <div className="rmw-stats">
        <Stat label="Requests waiting for you" value={v.requests.length} note={v.pastTarget.length?`${v.pastTarget.length} past target`:v.fresh.length?`${v.fresh.length} new today`:'all within target'} alert={v.pastTarget.length>0}/>
        <Stat label="Meetings in the next 7 days" value={v.upcoming.length} note={v.today.length?`${v.today.length} today`:'none today'}/>
        <Stat label="Meetings to document" value={v.toDocument.length} note="last 30 days" alert={v.toDocument.length>0}/>
        <Stat label="Maturities and reviews" value={v.soon.length} note="next 14 days"/>
      </div>

      <form className="rmw-book-ask" onSubmit={e=>{e.preventDefault();ask(question);}}>
        <label htmlFor="rm-book-question"><Icon name="spark"/>Ask across your book</label>
        <div className="rmw-book-ask-row"><input id="rm-book-question" data-testid="rm-book-question" value={question} maxLength={300} placeholder="For example: who has a fixed deposit maturing this month?"
          onChange={e=>setQuestion(e.target.value)}/><button className="primary-button" disabled={!question.trim()}>Ask</button></div>
        <div className="rmw-book-chips">{BOOK_QUESTIONS.map(q=><button key={q} type="button" className="rmw-mini" onClick={()=>ask(q)}>{q}</button>)}</div>
      </form>

      <div className="rmw-today-grid">
        <section className="rmw-card">
          <div className="rmw-card-head"><h3><Icon name="calendar"/>Meetings</h3></div>
          {data.meetingRequests.length>0&&<><h4 className="rmw-subhead">Meeting requests to answer</h4>
            <ul className="rmw-list" data-testid="rm-today-meeting-requests">{data.meetingRequests.map(r=><li key={r.request_id}><div><strong>{r.client}</strong>
              <small>{r.topics.map(topicLabel).join(', ')}{r.overdue?<span className="rmw-late"> · overdue, was due {r.respond_by_text}</span>
                :r.proposal_lapsed?' · your proposed time lapsed':` · respond by ${r.respond_by_text}`}{r.isNew&&<span className="rmw-chip info rmw-new">New</span>}</small></div>
              <div className="rmw-list-actions"><button className="rmw-mini" data-testid="rm-today-answer" onClick={()=>openSchedule('requests')}><Icon name="calendar"/>Answer</button></div></li>)}</ul>
            {v.upcoming.length>0&&<h4 className="rmw-subhead">Next 7 days</h4>}</>}
          {!v.upcoming.length?<p className="rm-none">No confirmed meetings in the next 7 days.</p>:
          <ul className="rmw-list">{v.upcoming.map(a=><li key={a.appointment_id}><div><strong>{a.client}</strong>
            <small>{a.date===day?'Today':formatDate(a.date,{weekday:'short',year:undefined})} · {timeOf(a.when)} · {FORMAT_WORDS[a.format]}{a.purpose?` · ${a.purpose}`:''}</small></div>
            <div className="rmw-list-actions"><button className="rmw-mini" data-testid="rm-today-brief" onClick={()=>brief(a)}><Icon name="spark"/>Brief me</button></div></li>)}</ul>}
          {v.toDocument.length>0&&<><h4 className="rmw-subhead">To document</h4>
            <ul className="rmw-list">{v.toDocument.map(a=><li key={a.appointment_id}><div><strong>{a.client}</strong>
              <small>{formatDate(a.date,{weekday:'short'})} · {timeOf(a.when)} · {FORMAT_WORDS[a.format]}{a.purpose?` · ${a.purpose}`:''}</small></div>
              <div className="rmw-list-actions"><button className="rmw-mini" data-testid="rm-document-meeting" onClick={()=>documentMeeting(a)}><Icon name="document"/>Document this meeting</button></div></li>)}</ul></>}
        </section>

        <section className="rmw-card">
          <div className="rmw-card-head"><h3><Icon name="clip"/>Requests waiting for you</h3></div>
          {!v.requests.length?<p className="rm-none">Nothing waiting for you.</p>:
          <ul className="rmw-list">{v.requests.slice(0,6).map(t=><li key={t.reference}><div><strong>{t.client} · {t.category_label}</strong>
            <small>{receivedText(t.age_hours)}{t.overdue?<span className="rmw-late"> · past target</span>:` · due ${t.target_sgt}`}{(t.age_hours??99)<24&&<span className="rmw-chip info rmw-new">New</span>}</small>
            {t.summary&&<span className="rmw-clamp">{t.summary}</span>}</div>
            <div className="rmw-list-actions"><button className="rmw-mini" onClick={()=>onAsk(`Draft a reply to ${t.client} about ${t.reference}`,{intent:'draft_message',context:{client_id:t.customer_id,ticket_ref:t.reference}})}><Icon name="chat"/>Draft a reply</button></div></li>)}</ul>}
        </section>

        <section className="rmw-card">
          <div className="rmw-card-head"><h3><Icon name="bell"/>Coming up</h3></div>
          {!v.soon.length&&!v.overdueTasks.length?<p className="rm-none">No maturities, reviews or overdue tasks in the next 14 days.</p>:
          <ul className="rmw-list">{[...v.overdueTasks,...v.soon].map(f=><li key={f.item_key}><div><strong>{f.client} · {f.title}</strong>
            <small className={f.overdue?'rmw-late':''}>{f.overdue?'Overdue · was due ':'Due '}{f.due_text}</small></div>
            <div className="rmw-list-actions"><button className="rmw-mini" onClick={()=>onAsk(`What do I need to know about ${f.client}'s ${f.title.replace(/^Your task: /,'')} (due ${f.due_text})?`,{intent:'client_overview',context:{client_id:f.customer_id}})}><Icon name="spark"/>Ask</button></div></li>)}</ul>}
        </section>
      </div>
    </>}
  </div>;
}

function Stat({label,value,note,alert}) {
  return <div className={`rmw-stat ${alert?'alert':''}`}><strong>{value}</strong><span>{label}</span><small>{note}</small></div>;
}
