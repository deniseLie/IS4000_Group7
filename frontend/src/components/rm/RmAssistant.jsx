import {useEffect,useRef,useState} from 'react';
import Icon from '../Icon';
import RmAnswer from './RmAnswer';
import {askAssistant,contextAfter,conversationPayload,display,historyFor,requestContext,rmGet,rmPut} from '../../lib/rm';
import {redact} from '../../lib/chatTools';

const STARTERS=[
  {icon:'clip',title:'Ticket triage',detail:'What needs my attention today?',message:'What needs my attention today?',intent:'ticket_inbox'},
  {icon:'bell',title:'Follow-up priorities',detail:'Who should I follow up with this week?',message:'Which clients should I follow up with this week, and why?',intent:'priorities'},
  {icon:'calendar',title:'Prepare a meeting',detail:'Brief me for my next meeting.',message:'What meetings do I have in the next two weeks?',intent:'schedule'},
  {icon:'document',title:'Document a call',detail:'Paste your call notes for a client.',draft:'Notes from my call today with '},
];

// The RM assistant: one conversation about one active client at a time (or the whole book). The active client is
// always shown; choosing another client starts a clean context so nothing carries over between clients.
export default function RmAssistant({token,onSource,messages,setMessages,context,setContext,ask,onAsked,advisor,onNavigate,conversationId,onNew,onSaved,historyOpen,onHistory,notice,onNotice}) {
  const [input,setInput]=useState('');
  const [busy,setBusy]=useState(false),[status,setStatus]=useState('');
  const [book,setBook]=useState([]);
  const list=useRef(null),composer=useRef(null),request=useRef(null);
  useEffect(()=>{
    const controller=new AbortController();
    (async()=>{try{setBook((await rmGet('/book',await token(),controller.signal)).data);}catch{}})();
    return ()=>{controller.abort();request.current?.abort();};
  },[]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(()=>{list.current?.scrollTo({top:list.current.scrollHeight,behavior:'smooth'});},[messages,busy]);
  useEffect(()=>{
    if(!ask)return;
    if(ask.prefill){if(ask.context.client_id&&ask.context.client_id!==context.client_id)chooseClient(ask.context.client_id);setInput(ask.message);requestAnimationFrame(()=>composer.current?.focus());}
    else send(ask.message,{intent:ask.intent,context:ask.context});
    onAsked();
  },[ask?.key]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(()=>{const box=composer.current;if(box){box.style.height='auto';box.style.height=`${Math.min(box.scrollHeight,200)}px`;}},[input]);

  const clientName=id=>book.find(c=>c.customer_id===id)?.name||null;
  // Save the conversation after each reply, so Sophie can leave and reopen it from Past conversations.
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
    const shown=redact(message);
    const user={role:'user',content:shown,client_id:ctx.client_id??null};
    const before=[...messages,user];
    setMessages([...before,{role:'assistant',pending:true}]);setInput('');setBusy(true);setStatus('Reading your records…');
    const controller=new AbortController();request.current=controller;
    try {
      const reply=await askAssistant({message:shown,history:historyFor(messages),context:requestContext(ctx),intent:options.intent||null},await token(),setStatus,controller.signal);
      const next=contextAfter(ctx,reply);
      setContext(next);
      const list=[...before,{role:'assistant',reply,client_id:next.client_id,ask:{message:shown,intent:reply.intent}}];
      setMessages(list);save(list,next);
    } catch(failure) {
      if(failure.name!=='AbortError')setMessages([...before,{role:'assistant',error:true,content:failure.message}]);
    } finally {setBusy(false);setStatus('');request.current=null;}
  }
  function choose(option,turn) {
    // Options that name a client, ticket, meeting or note re-ask the same question with that choice; others are replies.
    const pick={...(option.client_id&&{client_id:option.client_id}),...(option.ticket_ref&&{ticket_ref:option.ticket_ref}),
      ...(option.appointment_id&&{appointment_id:option.appointment_id}),...(option.note_id&&{note_id:option.note_id})};
    if(Object.keys(pick).length)send(turn.ask.message,{context:pick,intent:turn.ask.intent==='access_check'?null:turn.ask.intent});
    else send(option.message||option.label);
  }
  const lastIndex=messages.length-1;
  const active=context.client_id?clientName(context.client_id)||'Selected client':null;
  return <>
    <div className="chat-toolbar rm-toolbar">
      <label className="pill-select rm-client-select" title="The client the assistant is working on"><Icon name="user"/>
        <select aria-label="Active client" data-testid="rm-active-client" value={context.client_id||''} disabled={busy} onChange={e=>chooseClient(Number(e.target.value)||null)}>
          <option value="">Whole book</option>{book.map(c=><option key={c.customer_id} value={c.customer_id}>{c.name}</option>)}</select><Icon name="chevron"/></label>
      {context.ticket_ref&&<span className="rm-context-chip">Ticket {context.ticket_ref}<button type="button" aria-label="Clear ticket" onClick={()=>setContext({...context,ticket_ref:null})}><Icon name="close"/></button></span>}
      {context.appointment_id&&<span className="rm-context-chip">Meeting selected<button type="button" aria-label="Clear meeting" onClick={()=>setContext({...context,appointment_id:null})}><Icon name="close"/></button></span>}
      {context.documentation&&!context.documentation.note_id&&<span className="rm-context-chip warn">Documentation in progress</span>}
      {context.draft&&<span className="rm-context-chip warn">Draft revision {context.draft.revision}</span>}
      <span className="toolbar-spacer"/>
      <button className="bank-chat-logout new-chat" data-testid="rm-new-chat" disabled={busy} onClick={onNew}><Icon name="plus"/>New conversation</button>
      <button className={`recent-chats ${historyOpen?'active':''}`} data-testid="rm-open-history" aria-expanded={historyOpen} onClick={onHistory}><Icon name="history"/>Past conversations</button>
    </div>
    <section className="bank-chat-window" aria-label="RM assistant conversation">
      {notice&&<p className="chat-notice-bar" role="status"><Icon name="info"/><span>{notice}</span><button className="icon-button" aria-label="Dismiss message" onClick={()=>onNotice('')}><Icon name="close"/></button></p>}
      {active&&<p className="chat-notice-bar rm-active" role="status" data-testid="rm-active-banner"><Icon name="user"/><span>Working on <strong>{active}</strong>. Follow-up questions stay with this client; pick another client to switch.</span></p>}
      <div ref={list} className={`bank-chat-messages ${messages.length?'has-messages':''}`} aria-live="polite">
        {!messages.length&&<div className="bank-chat-welcome">
          <h1 className="welcome-signed-in">{advisor?`Hi ${display(advisor.name).split(' ')[0]}, what are we working on?`:'What are we working on?'}</h1>
          <p className="chat-greeting">I summarise and draft from your clients’ records. You decide, approve and send.</p>
          <p>Every point shows where it came from and whether it is a recorded fact, something the client said, my suggestion or a gap. I only work with clients in your book.</p>
          <div className="bank-chat-suggestions">{STARTERS.map(s=><button key={s.title} onClick={()=>s.draft?(setInput(s.draft),requestAnimationFrame(()=>composer.current?.focus())):send(s.message,{intent:s.intent})}>
            <Icon name={s.icon}/><strong>{s.title}</strong><span>{s.detail}</span><span className="topic-arrow" aria-hidden="true">↗</span></button>)}</div>
          <div className="quick-help"><span>Or open</span><button onClick={()=>onNavigate('clients')}>My clients <span aria-hidden="true">↗</span></button><button onClick={()=>onNavigate('schedule')}>Schedule <span aria-hidden="true">↗</span></button></div>
        </div>}
        {messages.map((m,i)=><div key={i} data-testid={`rm-message-${m.role}`} className={`bank-chat-message ${m.role}`}>
          {m.role==='assistant'&&<div className="bank-chat-avatar"><Icon name="spark"/></div>}
          <div className={`bank-chat-bubble ${m.error?'error':''}`}>
            {m.role==='assistant'&&<div className="answer-heading">RM assistant{m.reply&&<small className="answer-origin">{m.reply.origin==='ai'?'AI-generated from records':m.reply.origin==='access_check'?'Access check':'From records'}</small>}
              {m.client_id&&<small className="answer-origin">{clientName(m.client_id)}</small>}</div>}
            {m.role==='user'&&<span className="answer-text">{m.content}</span>}
            {m.pending&&<span className="answer-text typing" role="status"><span className="typing-dots" aria-hidden="true"><i/><i/><i/></span>{status}</span>}
            {m.error&&<span className="answer-text">{m.content}</span>}
            {m.reply&&<RmAnswer reply={m.reply} onSource={onSource} onOption={option=>choose(option,m)} token={token} latest={i===lastIndex&&!busy&&!m.restored}
              onAsk={text=>send(text,{intent:'draft_message'})}
              onDraftChange={value=>setContext(c=>({...c,draft:value}))}
              onDocumentationChange={value=>setContext(c=>({...c,documentation:value?{...(c.documentation||{}),...value}:null,note_id:value?.note_id||c.note_id}))}/>}
          </div></div>)}
      </div>
      <div className="composer-area"><form className="bank-chat-composer" onSubmit={e=>{e.preventDefault();send();}}>
        <textarea ref={composer} id="rm-composer" aria-label="Message to the RM assistant" rows={1} value={input} maxLength={8000} disabled={busy}
          placeholder={active?`Ask about ${active}, paste call notes, or ask for a draft…`:'Ask about your book, a client, a ticket or a meeting…'}
          onChange={e=>setInput(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();send();}}}/>
        <button className="composer-send" data-testid="rm-send" aria-label="Send" disabled={busy||!input.trim()}><Icon name="send"/></button>
      </form><div className="composer-note"><span><Icon name="shield"/>AI output can contain errors. Check sources before you act. Nothing is sent to a client or saved without your approval.</span></div></div>
    </section>
  </>;
}
