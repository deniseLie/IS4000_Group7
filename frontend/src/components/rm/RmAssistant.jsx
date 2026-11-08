import {useEffect,useRef,useState} from 'react';
import Icon from '../Icon';
import RmAnswer from './RmAnswer';
import {pageStep,STARTER_GROUPS,meetingSuggestions,meetingTitle,openMeetingPage,askAssistant,contextAfter,conversationPayload,display,historyFor,nextSteps,openSchedule,requestContext,rmGet,rmPut,slashActions,todayAction,todayList,todayView} from '../../lib/rm';
import {sharedTopicRows} from '../../lib/talkingPoints';
import {formatDateTime} from '../../lib/format';
import {redact} from '../../lib/chatTools';
import {MEETING_STEPS,meetingRequest,meetingStep} from '../../lib/meetingLifecycle';



// The RM assistant: one conversation about one active client at a time (or the whole book). The active client is
// always shown; choosing another client starts a clean context so nothing carries over between clients.
// The home screen leads with what needs her now (the same ranking as Today), then the starters grouped by the moment
// of her day; every answer ends with one suggested next step. No microphone: notes are typed or pasted.

export default function RmAssistant({token,chatCount,messages,setMessages,context,setContext,ask,onAsked,advisor,onNavigate,conversationId,onNew,onSaved,historyOpen,onHistory,notice,onNotice}) {
  const [input,setInput]=useState('');
  const [busy,setBusy]=useState(false),[status,setStatus]=useState(''),[parts,setParts]=useState([]);  // sections streamed before the answer
  const [book,setBook]=useState([]);
  const list=useRef(null),composer=useRef(null),request=useRef(null);
  const [tab,setTab]=useState(0),[now,setNow]=useState(null),[resume,setResume]=useState(null),[meeting,setMeeting]=useState(null),[slashAt,setSlashAt]=useState(0);
  const file=useRef(null);
  useEffect(()=>{
    const controller=new AbortController();
    (async()=>{try{setBook((await rmGet('/book',await token(),controller.signal)).data);}catch{}})();
    // "Needs you now" is the top of Today's list (same helper, same order); "Continue" is the meeting in progress.
    (async()=>{try{const auth=await token(),s=controller.signal,optional=p=>p.catch(()=>null);
      const [p,a,sh,rem,tk]=await Promise.all([rmGet('/priorities',auth,s),optional(rmGet('/appointments?past_days=30',auth,s)),optional(rmGet('/shared-topics',auth,s)),
        optional(meetingRequest('/capabilities','GET',null,auth,s).then(c=>c.enabled?meetingRequest('/reminders','GET',null,auth,s):[])),optional(rmGet('/tickets',auth,s))]);
      const appointments=a?.data||[],reminders=rem||[];
      const day=new Date().toISOString().slice(0,10);
      setNow(todayList({priorities:p.items||[],reminders,toDocument:todayView({appointments},day).toDocument,shared:sharedTopicRows(sh?.data),tickets:tk?.data?.rm_owned}).slice(0,3));
      const r=reminders.find(x=>x.kind!=='preparation')||reminders[0];
      if(r){const m=appointments.find(x=>x.appointment_id===r.meeting_id);setResume({...r,title:m?meetingTitle(m.purpose,m.topics):null,step:r.kind==='preparation'?0:2});}
    }catch{setNow([]);}})();
    return ()=>{controller.abort();request.current?.abort();};
  },[]);  // eslint-disable-line react-hooks/exhaustive-deps
  // A selected meeting stays pinned above the conversation with its four steps (the same steps as the meeting page).
  useEffect(()=>{
    const id=context.appointment_id;if(!id){setMeeting(null);return;}
    const controller=new AbortController();
    (async()=>{try{const auth=await token(),[a,l]=await Promise.all([meetingRequest(`/${id}/appointment`,'GET',null,auth,controller.signal),meetingRequest(`/${id}/lifecycle`,'GET',null,auth,controller.signal).catch(()=>null)]);
      if(!controller.signal.aborted)setMeeting({id,title:meetingTitle(a.purpose,a.topics),when:a.starts_at,step:meetingStep(l?.stage)});}catch{if(!controller.signal.aborted)setMeeting({id,title:'Selected meeting',when:null,step:0});}})();
    return ()=>controller.abort();
  },[context.appointment_id]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(()=>{list.current?.scrollTo({top:list.current.scrollHeight,behavior:'smooth'});},[messages,busy]);
  useEffect(()=>{
    if(!ask)return;
    if(ask.prefill){
      // A prefilled message (Document a call / this meeting) selects the client, and the meeting when one is given.
      if(ask.context.client_id&&ask.context.client_id!==context.client_id)chooseClient(ask.context.client_id);
      if(ask.context.appointment_id)setContext(c=>({...c,client_id:ask.context.client_id||c.client_id,appointment_id:ask.context.appointment_id,documentation:null,draft:null}));
      setInput(ask.message);requestAnimationFrame(()=>{const box=composer.current;if(box){box.focus();box.setSelectionRange(box.value.length,box.value.length);}});
    }
    else send(ask.message,{intent:ask.intent,context:ask.context});
    onAsked();
  },[ask?.key]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(()=>{const box=composer.current;if(box){box.style.height='auto';box.style.height=`${Math.min(box.scrollHeight,200)}px`;}},[input]);

  const clientName=id=>book.find(c=>c.customer_id===id)?.name||null;
  // Save the conversation after each reply, so Sophie can leave and reopen it from Recent chats.
  async function save(list,ctx) {
    try {await rmPut(`/conversations/${conversationId}`,conversationPayload(list,ctx),await token());onSaved?.();}
    catch(failure) {onNotice?.(`This conversation could not be saved: ${failure.message}`);}
  }
  function chooseClient(id) {
    // Switching client clears ticket, meeting, draft and documentation context (RM2: no carry-over between clients).
    setContext({client_id:id||null,ticket_ref:null,appointment_id:null,note_id:null,draft:null,documentation:null});
  }
  async function send(text,options={}) {
    const message=(text??input).trim();
    if(!message||busy)return;
    const ctx={...context,...(options.context||{})};
    if(options.context?.client_id&&options.context.client_id!==context.client_id)Object.assign(ctx,{draft:null,documentation:null,ticket_ref:options.context.ticket_ref||null,appointment_id:options.context.appointment_id||null,note_id:options.context.note_id||null});
    if(options.context?.appointment_id||options.context?.client_id)setContext(ctx);  // the chosen client and meeting show (and pin) at once
    const shown=redact(message);
    const user={role:'user',content:shown,client_id:ctx.client_id??null};
    const before=[...messages,user];
    setMessages([...before,{role:'assistant',pending:true}]);setInput('');setBusy(true);setStatus('Reading your records…');setParts([]);
    const controller=new AbortController();request.current=controller;
    try {
      const reply=await askAssistant({message:shown,history:historyFor(messages),context:requestContext(ctx),intent:options.intent||null},await token(),setStatus,
        controller.signal,part=>setParts(previous=>[...previous,part]));
      const next=contextAfter(ctx,reply);
      setContext(next);
      const list=[...before,{role:'assistant',reply,client_id:next.client_id,ask:{message:shown,intent:reply.intent}}];
      setMessages(list);save(list,next);
    } catch(failure) {
      if(failure.name!=='AbortError')setMessages([...before,{role:'assistant',error:true,content:failure.message}]);
    } finally {setBusy(false);setStatus('');setParts([]);request.current=null;}
  }
  function choose(option,turn) {
    // Options that name a client, ticket, meeting or note re-ask the same question with that choice; others are replies.
    const pick={...(option.client_id&&{client_id:option.client_id}),...(option.ticket_ref&&{ticket_ref:option.ticket_ref}),
      ...(option.appointment_id&&{appointment_id:option.appointment_id}),...(option.note_id&&{note_id:option.note_id})};
    if(Object.keys(pick).length)send(turn.ask.message,{context:pick,intent:turn.ask.intent==='access_check'?null:turn.ask.intent});
    else send(option.message||option.label);
  }
  function start(s) {
    if(s.meetingPage&&meeting)return openMeetingPage(meeting.id,clientName(context.client_id));
    if(s.navigate)return onNavigate(s.navigate);
    if(s.draft){setInput(s.draft);requestAnimationFrame(()=>composer.current?.focus());return;}
    send(s.message,{intent:s.intent});
  }
  const lastIndex=messages.length-1;
  const slash=slashActions(input);
  function pickSlash(s){setInput('');setSlashAt(0);start(s);}
  // Attach a notes or transcript text file: its text goes into the box for her to check before sending.
  async function attach(e){const f=e.target.files?.[0];e.target.value='';if(!f)return;
    if(f.size>200000){onNotice?.('That file is too large to paste here. Upload transcripts on the meeting page instead.');return;}
    const text=(await f.text()).slice(0,7800);setInput(i=>`${i?`${i}\n`:''}${text}`);requestAnimationFrame(()=>composer.current?.focus());}
  function act(x){const a=todayAction(x);return a.meeting?openMeetingPage(a.meeting,x.client):a.schedule?openSchedule(a.schedule):send(a.message,{intent:a.intent,context:a.context});}
  const active=context.client_id?clientName(context.client_id)||'Selected client':null;
  return <>
    <div className="chat-toolbar rm-toolbar">
      <label className="pill-select rm-client-select" title="The client the assistant is working on"><Icon name="user"/>
        <select aria-label="Active client" data-testid="rm-active-client" value={context.client_id||''} disabled={busy} onChange={e=>chooseClient(Number(e.target.value)||null)}>
          <option value="">All clients</option>{book.map(c=><option key={c.customer_id} value={c.customer_id}>{c.name}</option>)}</select><Icon name="chevron"/></label>
      {context.ticket_ref&&<span className="rm-context-chip">Ticket {context.ticket_ref}<button type="button" aria-label="Clear ticket" onClick={()=>setContext({...context,ticket_ref:null})}><Icon name="close"/></button></span>}
      {context.appointment_id&&!meeting&&<span className="rm-context-chip">Meeting selected<button type="button" aria-label="Clear meeting" onClick={()=>setContext({...context,appointment_id:null})}><Icon name="close"/></button></span>}
      {context.documentation&&!context.documentation.note_id&&<span className="rm-context-chip warn">Documentation in progress</span>}
      {context.draft&&<span className="rm-context-chip warn">Draft revision {context.draft.revision}</span>}
      <span className="toolbar-spacer"/>
      <button className="bank-chat-logout new-chat" data-testid="rm-new-chat" disabled={busy} onClick={onNew}><Icon name="plus"/>New conversation</button>
      <button className={`recent-chats ${historyOpen?'active':''}`} data-testid="rm-open-history" aria-expanded={historyOpen} onClick={onHistory}><Icon name="history"/>Recent chats{chatCount!==null&&chatCount!==undefined&&<span className="count">{chatCount}</span>}</button>
    </div>
    {meeting&&<section className="rm-pinned" data-testid="rm-pinned-meeting" aria-label="Meeting in focus">
      <div><span className="eyebrow">Meeting{active?` · ${active}`:''}</span><strong>{meeting.title}</strong>{meeting.when&&<span className="rm-small"> · {formatDateTime(meeting.when)}</span>}</div>
      <ol className="rm-pinned-steps" aria-label="Meeting steps">{MEETING_STEPS.map(([key,label],i)=><li key={key} aria-current={i===meeting.step?'step':undefined}>
        <button type="button" className={`rm-pill ${i<meeting.step?'done':i===meeting.step?'now':''}`} onClick={()=>openMeetingPage(meeting.id,active)}>{i<meeting.step?'✓ ':i===meeting.step?'● ':''}{label}</button></li>)}</ol>
      <button type="button" className="rm-plain rm-pinned-open" onClick={()=>openMeetingPage(meeting.id,active)}>Open in Schedule ↗</button></section>}
    <section className="bank-chat-window" aria-label="RM assistant conversation">
      {notice&&<p className="chat-notice-bar" role="status"><Icon name="info"/><span>{notice}</span><button className="icon-button" aria-label="Dismiss message" onClick={()=>onNotice('')}><Icon name="close"/></button></p>}
      {active&&<p className="chat-notice-bar rm-active" role="status" data-testid="rm-active-banner"><Icon name="user"/><span>Working on <strong>{active}</strong>. Follow-up questions stay with this client; pick another client to switch.</span></p>}
      <div ref={list} className={`bank-chat-messages ${messages.length?'has-messages':''}`} aria-live="polite">
        {!messages.length&&<div className="bank-chat-welcome">
          <h1 className="welcome-signed-in">{(h=>h<12?'Good morning':h<18?'Good afternoon':'Good evening')(Number(new Intl.DateTimeFormat('en-SG',{hour:'numeric',hour12:false,timeZone:'Asia/Singapore'}).format(new Date())))}{advisor?`, ${display(advisor.name).split(' ')[0]}`:''}</h1>
          <p className="chat-greeting">What are we working on? I summarise and draft from your clients’ records. You decide, approve and send.</p>
          {now?.length>0&&<section className="rm-now" data-testid="rm-needs-now" aria-label="Needs you now"><div className="rm-now-head"><strong>Needs you now</strong><span className="rm-small">from Today, most important first</span>
            <button type="button" className="rm-plain rm-now-open" onClick={()=>onNavigate('today')}>Open Today ↗</button></div>
            {now.map((x,i)=><div key={x.key} className="rm-now-row"><span className={`rm-dot ${x.tone}`} aria-label={x.label}/><span className="rm-now-text"><strong>{x.client}</strong> · {x.title}{x.why&&<span className="rm-now-why"> · {x.why}</span>}</span>
              <button type="button" className="bank-chat-logout" onClick={()=>act(x)}>{todayAction(x).label}</button></div>)}</section>}
          {resume&&<section className="rm-now rm-continue" data-testid="rm-continue" aria-label="Continue where you left off"><div><span className="eyebrow">Continue where you left off</span>
            <strong>{resume.client} · {resume.title||(resume.kind==='preparation'?'Prepare for the meeting':'Document the meeting')}</strong>
            <span className="rm-continue-steps"><span className="rm-steps" aria-hidden="true">{[0,1,2,3].map(k=><i key={k} className={k<resume.step?'done':k===resume.step?'now':''}/>)}</span><small>Step {resume.step+1} of 4: {MEETING_STEPS[resume.step][1]}</small></span></div>
            <button type="button" className="bank-chat-logout" onClick={()=>openMeetingPage(resume.meeting_id,resume.client)}>Continue →</button></section>}
          <nav className="page-tabs rm-starter-tabs" role="tablist" aria-label="What I can do">{STARTER_GROUPS.map((g,i)=><button key={g.label} type="button" role="tab" aria-selected={tab===i} data-testid={`rm-starter-tab-${i}`} onClick={()=>setTab(i)}>{g.label}</button>)}</nav>
          <div className="bank-chat-suggestions">{STARTER_GROUPS[tab].starters.map(s=><button key={s.title} onClick={()=>start(s)}>
            <Icon name={s.icon}/><strong>{s.title}</strong><span>{s.detail}</span><span className="topic-arrow" aria-hidden="true">↗</span></button>)}</div>
        </div>}
        {messages.map((m,i)=><div key={i} data-testid={`rm-message-${m.role}`} className={`bank-chat-message ${m.role}`}>
          {m.role==='assistant'&&<div className="bank-chat-avatar"><Icon name="spark"/></div>}
          <div className={`bank-chat-bubble ${m.error?'error':''}`}>
            {m.role==='assistant'&&!(m.reply?.sections?.length&&m.reply.status!=='denied')&&<div className="answer-heading">RM assistant{m.reply?.origin==='access_check'&&<small className="answer-origin">Access check</small>}</div>}
            {m.role==='user'&&<span className="answer-text">{m.content}</span>}
            {m.pending&&<span className="answer-text typing" role="status"><span className="typing-dots" aria-hidden="true"><i/><i/><i/></span>{status}</span>}
            {/* Like the client side: the three dots stay until the whole answer is ready (no part-by-part preview). */}
            {m.error&&<span className="answer-text">{m.content}</span>}
            {m.reply&&<RmAnswer reply={m.reply} link={pageStep(m.reply)&&{label:pageStep(m.reply).link||pageStep(m.reply).label,go:()=>start(pageStep(m.reply))}} onEdit={next=>{const list=messages.map((x,k)=>k===i?{...x,reply:next}:x);setMessages(list);save(list,context);}} client={m.client_id?clientName(m.client_id):null} onOption={option=>choose(option,m)} token={token} latest={i===lastIndex&&!busy&&!m.restored}
              onAsk={text=>send(text,{intent:'draft_message'})}
              onDraftChange={value=>setContext(c=>({...c,draft:value}))}
              onDocumentationChange={value=>setContext(c=>({...c,documentation:value?{...(c.documentation||{}),...value}:null,note_id:value?.note_id||c.note_id}))}/>}
            {m.reply&&i===lastIndex&&!busy&&!m.restored&&nextSteps(m.reply).length>0&&<div className="rm-next" data-testid="rm-next-steps"><span>Next:</span>{nextSteps(m.reply).map(n=><button key={n.label} type="button" className="rmw-mini" onClick={()=>start(n)}>{n.label}</button>)}</div>}
          </div></div>)}
      </div>
      <div className="composer-area">
        {meeting&&!busy&&<div className="rm-step-suggestions" aria-label="Suggestions for this step">{meetingSuggestions(meeting.step,active).map(x=><button key={x.label} type="button" className="rm-pill" onClick={()=>start(x)}>{x.label}</button>)}</div>}
        {slash.length>0&&<ul className="rm-slash" role="listbox" aria-label="Every action" data-testid="rm-slash">{slash.map((x,i)=><li key={x.title} role="option" aria-selected={i===slashAt}>
          <button type="button" onMouseDown={e=>e.preventDefault()} onClick={()=>pickSlash(x)}><Icon name={x.icon}/><span><strong>{x.title}</strong><small>{x.detail}</small></span><em>{x.group}</em></button></li>)}</ul>}
        <form className="bank-chat-composer" onSubmit={e=>{e.preventDefault();if(slash.length)return pickSlash(slash[slashAt]||slash[0]);send();}}>
        <textarea ref={composer} id="rm-composer" aria-label="Message to the RM assistant" rows={1} value={input} maxLength={8000} disabled={busy}
          placeholder={meeting?`Ask about ${active?`${active}’s`:'this'} meeting, or type / for actions`:active?`Ask about ${active}, or type / to see every action`:'Ask anything, or type / to see every action'}
          onChange={e=>{setInput(e.target.value);setSlashAt(0);}} onKeyDown={e=>{
            if(slash.length&&(e.key==='ArrowDown'||e.key==='ArrowUp')){e.preventDefault();setSlashAt(i=>(i+(e.key==='ArrowDown'?1:-1)+slash.length)%slash.length);return;}
            if(slash.length&&e.key==='Escape'){setInput('');return;}
            if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();if(slash.length)pickSlash(slash[slashAt]||slash[0]);else send();}}}/>
        <input ref={file} type="file" accept=".txt,.vtt,.md,text/plain,text/vtt" hidden onChange={attach}/>
        <button type="button" className="composer-attach" aria-label="Attach notes or a transcript file" title="Attach notes or a transcript (.txt, .vtt)" disabled={busy} onClick={()=>file.current?.click()}><Icon name="clip"/></button>
        <button className="composer-send" data-testid="rm-send" aria-label="Send" disabled={busy||!input.trim()}><Icon name="send"/></button>
      </form>
      <div className="composer-note"><span>{messages.length?<><Icon name="shield"/>AI output can contain errors. Check sources before you act. Nothing is sent to a client or saved without your approval.</>
        :<>Tip: <kbd>/</kbd> lists every action · ask “what’s next for Michelle?” any time · AI output can contain errors; nothing is sent without your approval.</>}</span></div></div>
    </section>
  </>;
}
