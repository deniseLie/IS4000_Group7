import { useEffect, useRef, useState } from 'react';
import Icon from './components/Icon';
import Panel from './components/Panel';
import Answer from './components/Answer';
import { supabase } from './lib/supabase';
import { boundedHistory, readEvents, redact, supportSummary } from './lib/chatTools';

const API_URL = import.meta.env.VITE_API_URL || '/api';
const GUEST_SESSION = globalThis.crypto?.randomUUID?.();  // needs a secure context (https or localhost); correlation id for guest requests; signed-in sessions use the token's session_id
const ERRORS = {
  auth_invalid:'Session expired. Please sign in again.', auth_expired:'Session expired. Please sign in again.', auth_required:'Please sign in to use your account.',
  profile_missing:'No demo customer profile is linked to this login. Use General help or ask the project owner to link a synthetic profile.',
  personal_unavailable:'Personal demo records are unavailable. Select General help to continue.',
  rate_limited:'Too many messages in a short time. Please wait a moment and retry.', payload_too_large:'This conversation is too long to send. Start a new chat and try again.',
};
const DISCLAIMER = 'Demo AI assistant. General information only, not advice or a contract. Personal records are synthetic. AI answers can contain errors.';

export default function Chat({session,onSignIn}) {
  const [messages,setMessages]=useState([]);
  const [input,setInput]=useState('');
  const [loading,setLoading]=useState(false);
  const [mode,setMode]=useState(session ? 'personal':'public');
  const [language,setLanguage]=useState('English');
  const [notice,setNotice]=useState('');
  const [summary,setSummary]=useState(null);
  const [approved,setApproved]=useState(false);
  const [copied,setCopied]=useState(false);
  const [copyError,setCopyError]=useState('');
  const [article,setArticle]=useState(null);
  const messageList=useRef(null);
  const composer=useRef(null);
  const request=useRef(null);
  const version=useRef(0);
  useEffect(()=>{
    const list=messageList.current;
    if(messages.length&&list)list.scrollTo({top:list.scrollHeight,behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});
  },[messages]);
  useEffect(()=>()=>request.current?.abort(),[]);

  function reset() {
    version.current++;request.current?.abort();request.current=null;
    setMessages([]);setInput('');setLoading(false);setNotice('New chat started. Previous context has been cleared.');
    setSummary(null);setApproved(false);setCopied(false);setCopyError('');setArticle(null);
  }

  async function endSession() {  // records auth.session_ended; sign-out goes ahead even if this fails
    try {
      const {data}=await supabase.auth.getSession();
      if(data.session)await fetch(`${API_URL}/auth/session-end`,{method:'POST',headers:{Authorization:`Bearer ${data.session.access_token}`}});
    } catch {}
  }

  async function send(e) {
    e.preventDefault();
    const question=input.trim();if(!question||loading)return;
    const safe=redact(question);
    if(safe!==question) {
      setInput('');setNotice('Private details were removed. Never share passwords, PINs, OTPs, CVVs or full card numbers in chat. Please ask again without them. If your real card is lost or stolen, contact your bank immediately through its official app or website.');return;
    }
    const visible=[...messages,{role:'user',content:safe}];
    const history=boundedHistory(visible);
    if(history.length<visible.filter(m=>!m.error).length)setNotice('Older messages are no longer sent. Please restate any older details that still matter.');
    setMessages([...visible,{role:'assistant',content:''}]);setInput('');setLoading(true);
    const epoch=version.current;const controller=new AbortController();request.current=controller;
    try {
      const headers={'Content-Type':'application/json',...(GUEST_SESSION&&{'X-Session-Id':GUEST_SESSION})};
      if(session) {
        const {data}=await supabase.auth.getSession();
        if(!data.session)throw new Error('Session expired. Please sign in again.');
        headers.Authorization=`Bearer ${data.session.access_token}`;
      }
      const response=await fetch(`${API_URL}/chat`,{method:'POST',headers,body:JSON.stringify({messages:history,mode,language}),signal:controller.signal});
      if(!response.ok) {
        const code=await response.json().then(b=>b?.error?.code,()=>null);
        throw new Error(ERRORS[code]||'The banking service is unavailable. Please retry.');
      }
      if(!response.body)throw new Error('No response received.');
      await readEvents(response.body,(type,data)=>{
        if(epoch!==version.current)return;
        if(type==='answer') {
          const result=JSON.parse(data);
          setMessages([...visible,{role:'assistant',content:result.answer,...result}]);
        }
      });
    } catch(err) {
      if(err.name!=='AbortError'&&epoch===version.current)setMessages([...visible,{role:'assistant',content:err.message,error:true}]);
    } finally { if(epoch===version.current){setLoading(false);request.current=null;} }
  }

  async function showSource(source) {
    if(!source.url)return;
    const epoch=version.current;
    try {
      const r=await fetch(`${API_URL}${source.url}`);if(!r.ok)throw new Error();
      const details=await r.json();
      if(epoch===version.current)setArticle(details);
    }catch{if(epoch===version.current)setNotice('Source unavailable. Please retry.');}
  }

  function draft(question) { setInput(question);composer.current?.focus(); }
  function prepareSummary() { setSummary(supportSummary(messages));setApproved(false);setCopied(false);setCopyError(''); }
  const topics=[
    {icon:'account',title:'Accounts & savings',detail:'Make sense of everyday banking.',question:'What happens if I withdraw my fixed deposit early?'},
    {icon:'card',title:'Cards & payments',detail:'Understand payments and charges.',question:'Does paying only the minimum on my credit card avoid interest?'},
    {icon:'loan',title:'Borrowing basics',detail:'Explore the different types of loans.',question:'What is the difference between a secured and unsecured loan?'},
  ];

  return <div className="bank-chat-page">
    <a className="skip-link" href="#chat-composer">Skip to message</a>
    <header className="bank-chat-header">
      <div className="bank-chat-brand"><span className="brand-name">DemoBank<span className="brand-dot">.</span></span><div className="brand-caption"><strong>Banking assistant</strong><span>AI-powered · Student project</span></div></div>
      <div className="chat-actions"><button className="bank-chat-logout" onClick={reset}><Icon name="plus"/>New chat</button><button className="bank-chat-logout support-action" onClick={prepareSummary}><Icon name="support"/><span>Talk to a person</span></button>
      {session?<button className="bank-chat-logout sign-in-action" onClick={async()=>{request.current?.abort();await endSession();try{await supabase.auth.signOut();}catch{setNotice('Unable to sign out. Please retry.');}}}>Sign out<Icon name="arrow"/></button>:<button className="bank-chat-logout sign-in-action" onClick={onSignIn}>Sign in<Icon name="arrow"/></button>}</div>
    </header>
    <div className="bank-chat-layout">
      <aside className="bank-sidebar" aria-label="Banking topics">
        <div className="sidebar-group"><span className="eyebrow">Your support</span><div className="sidebar-current"><Icon name="chat"/>Banking assistant</div><button onClick={()=>draft('How can I keep my details safe in chat?')}><Icon name="shield"/>Privacy & safety</button></div>
        <div className="sidebar-group"><span className="eyebrow">Explore topics</span>{topics.map(t=><button key={t.icon} onClick={()=>draft(t.question)}><Icon name={t.icon}/>{t.title}</button>)}</div>
        <div className="sidebar-note"><span className="small-rule"/><h2>A little clarity.<br/>A helpful next step.</h2><p>General guidance for your everyday banking questions.</p></div>
        <div className="sidebar-session"><span className="session-avatar"><Icon name="user"/></span><div>{session?'Demo account':'Guest session'}<small>{session?'Synthetic records only':'No account connected'}</small></div></div>
      </aside>
      <main className="chat-workspace">
        <div className="chat-settings"><label>Information <select aria-label="Information" value={mode} onChange={e=>{reset();setMode(e.target.value);}}><option value="public">General help</option>{session&&<option value="personal">Personal demo records</option>}</select></label>
          <label>Answer language <select aria-label="Answer language" value={language} onChange={e=>setLanguage(e.target.value)}>{['English','Chinese','Malay','Tamil'].map(l=><option key={l}>{l}</option>)}</select></label></div>
        <p className="language-note">The language choice applies to AI answers. Menus, safety guidance and notices remain in English. Translation quality is still being tested.</p>
        <section className="bank-chat-window" aria-label="Banking conversation">
          {notice&&<p className="chat-notice" role="status">{notice}</p>}
          <div ref={messageList} className={`bank-chat-messages ${messages.length?'has-messages':''}`} aria-live="polite" aria-relevant="additions text">
            {!messages.length&&<div className="bank-chat-welcome"><span className="eyebrow welcome-eyebrow">A little clarity goes a long way</span><h1>Banking questions.<br/><span>Clearly answered.</span></h1><p>Understand your accounts, cards and loans.<br/>Start with a question. We’ll take it from there.</p><div className="bank-chat-suggestions">{topics.map(t=><button key={t.icon} onClick={()=>draft(t.question)}><Icon name={t.icon}/><strong>{t.title}</strong><span>{t.detail}</span><span className="topic-arrow" aria-hidden="true">↗</span></button>)}</div><div className="quick-help"><span>Need help now?</span><button onClick={()=>draft('I lost my card')}>I lost my card <span aria-hidden="true">↗</span></button><button onClick={()=>draft('How do I find my statement?')}>Find my statement <span aria-hidden="true">↗</span></button></div></div>}
            {messages.map((m,i)=><div key={i} className={`bank-chat-message ${m.role}`} data-response-origin={m.response_origin} data-prompt-version={m.prompt_version}>
              {m.role==='assistant'&&<div className="bank-chat-avatar"><Icon name="spark"/></div>}
              <div className={`bank-chat-bubble ${m.error?'error':''}`}>
                {m.role==='assistant'&&<div className="answer-heading">Banking assistant{m.response_origin&&<small className="answer-origin">{m.response_origin==='ai'?'AI-generated':m.response_origin==='safety'?'Safety guidance':'Answer withheld'}</small>}</div>}
                <Answer message={m} pendingText={loading?'Checking the available information…':''} onSource={showSource}/>
                {m.disclaimer&&<small className="answer-disclaimer">{m.disclaimer}</small>}
              </div>
            </div>)}
          </div>
          <div className="composer-area"><form className="bank-chat-composer" onSubmit={send}>
            <input ref={composer} id="chat-composer" aria-label="Your message" value={input} onChange={e=>setInput(e.target.value)} disabled={loading} maxLength={4000} placeholder="Ask a banking question…"/>
            <div className="composer-tools"><span><Icon name="shield"/>Never share your PIN, password or OTP</span><button data-testid="send-message" disabled={loading||!input.trim()}>{loading?'Please wait':'Send'}<Icon name="send"/></button></div>
          </form><p className="bank-chat-disclaimer">{DISCLAIMER}</p>
          <details className="session-note"><summary>About this conversation</summary><p>Chat history is temporary. Refreshing or starting a new chat clears it. Older messages may be omitted from long chats. General guidance works without login. Sign in for synthetic account records.</p></details></div>
        </section>
        <footer className="bank-chat-footer"><span>{session?'Signed in for synthetic demo records':'Guest access — general information only'}</span><span>IS4000 · Coursework demo</span></footer>
      </main>
    </div>
    {summary!==null&&<Panel label="Human support" title="Prepare a support summary" onClose={()=>setSummary(null)}><p>This demo has no staffed support queue. For a real issue, use your bank’s official app or website. This draft is not sent anywhere.</p><textarea aria-label="Support summary" rows={7} value={summary} onChange={e=>{setSummary(e.target.value);setApproved(false);setCopied(false);}}/><label className="review-summary"><input type="checkbox" checked={approved} onChange={e=>setApproved(e.target.checked)}/> I reviewed the summary and removed private details</label><div className="panel-actions"><button className="primary-button" disabled={!approved} onClick={async()=>{try{setCopyError('');await navigator.clipboard.writeText(redact(summary));setCopied(true);}catch{setCopyError('Copy unavailable. Select and copy the draft manually.');}}}>Copy summary</button></div>{copied&&<p role="status">Copied. No ticket was sent.</p>}{copyError&&<p role="alert">{copyError}</p>}</Panel>}
    {article&&<Panel label="Source details" title={article.title} onClose={()=>setArticle(null)}><p className="source-notice">{article.notice}</p><p>{article.content}</p><p className="source-publisher">Publisher: {article.publisher}</p>
      {article.reference_url&&<p><a href={article.reference_url} target="_blank" rel="noopener noreferrer">Read the original source <span aria-hidden="true">↗</span></a></p>}
      {article.checked_on&&<p>Source checked: {article.checked_on}. Review due: {article.review_due}.</p>}
      {article.current===false&&<p role="status">This source needs review and is not used for new answers.</p>}
      <small>Version {article.version}</small></Panel>}
  </div>;
}
