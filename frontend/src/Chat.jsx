import { useEffect, useRef, useState } from 'react';
import { supabase } from './lib/supabase';
import { boundedHistory, readEvents, redact, supportSummary } from './lib/chatTools';

const API_URL = import.meta.env.VITE_API_URL || '/api';
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
  const [article,setArticle]=useState(null);
  const bottom=useRef(null);
  const request=useRef(null);
  const version=useRef(0);
  useEffect(()=>{bottom.current?.scrollIntoView({behavior:'smooth'});},[messages]);
  useEffect(()=>()=>request.current?.abort(),[]);

  function reset() {
    version.current++;request.current?.abort();request.current=null;
    setMessages([]);setInput('');setLoading(false);setNotice('New chat started. Previous context has been cleared.');
    setSummary(null);setApproved(false);setCopied(false);setArticle(null);
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
      const headers={'Content-Type':'application/json'};
      if(session) {
        const {data}=await supabase.auth.getSession();
        if(!data.session)throw new Error('Session expired. Please sign in again.');
        headers.Authorization=`Bearer ${data.session.access_token}`;
      }
      const response=await fetch(`${API_URL}/chat`,{method:'POST',headers,body:JSON.stringify({messages:history,mode,language}),signal:controller.signal});
      if(!response.ok)throw new Error(response.status===401?'Session expired. Please sign in again.':response.status===403?'No demo customer profile is linked to this login. Use General help or ask the project owner to link a synthetic profile.':'The banking service is unavailable. Please retry.');
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
    try {
      const r=await fetch(`${API_URL}${source.url}`);if(!r.ok)throw new Error();
      setArticle(await r.json());
    }catch{setNotice('Source unavailable. Please retry.');}
  }

  return <div className="bank-chat-page"><div className="bank-chat-layout">
    <header className="bank-chat-header"><div className="bank-chat-brand"><div className="bank-chat-icon">◆</div><div><h1>DemoBank</h1><span>AI Banking Assistant</span></div></div>
      <div className="chat-actions"><button className="bank-chat-logout" onClick={reset}>New chat</button><button className="bank-chat-logout" onClick={()=>{setSummary(supportSummary(messages));setApproved(false);setCopied(false);}}>Talk to a person</button>
      {session?<button className="bank-chat-logout" onClick={async()=>{request.current?.abort();try{await supabase.auth.signOut();}catch{setNotice('Unable to sign out. Please retry.');}}}>Sign out</button>:<button className="bank-chat-logout" onClick={onSignIn}>Sign in</button>}</div></header>
    <div className="chat-settings"><label>Information <select aria-label="Information" value={mode} onChange={e=>{reset();setMode(e.target.value);}}><option value="public">General help</option>{session&&<option value="personal">Personal demo records</option>}</select></label>
      <label>Answer language <select aria-label="Answer language" value={language} onChange={e=>setLanguage(e.target.value)}>{['English','Chinese','Malay','Tamil'].map(l=><option key={l}>{l}</option>)}</select></label></div>
    <section className="bank-chat-window"><p className="bank-chat-disclaimer">{DISCLAIMER} Never share login secrets here.</p>
      {notice&&<p className="chat-notice" role="status">{notice}</p>}
      <div className="bank-chat-messages" aria-live="polite">
        {!messages.length&&<div className="bank-chat-welcome"><h2>How can I help you today?</h2><p>General guidance works without login. Sign in for synthetic account records.</p><div className="bank-chat-suggestions">{['What does a pending transaction mean?','I lost my card','How do I find my statement?'].map(q=><button key={q} onClick={()=>setInput(q)}>{q}</button>)}</div></div>}
        {messages.map((m,i)=><div key={i} className={`bank-chat-message ${m.role}`}><div className={`bank-chat-bubble ${m.error?'error':''}`}>
          <span>{m.content||(loading?'Checking the available information…':'')}</span>
          {!!m.sources?.length&&<details><summary>Sources</summary><p>These sources were supplied to the assistant. A citation does not guarantee that every claim is correct.</p>{m.sources.map(s=><div key={s.id}>{s.url?<button className="source-link" onClick={()=>showSource(s)}>{s.title}</button>:<span>{s.title}</span>} <small>{s.version}</small></div>)}</details>}
          {m.disclaimer&&<small className="answer-disclaimer">{m.disclaimer}</small>}
        </div></div>)}<div ref={bottom}/>
      </div>
      <form className="bank-chat-composer" onSubmit={send}><input aria-label="Your message" value={input} onChange={e=>setInput(e.target.value)} disabled={loading} maxLength={4000} placeholder="Ask a banking question…"/><button disabled={loading||!input.trim()}>{loading?'Please wait':'Send'}</button></form>
      <p className="bank-chat-disclaimer">Chat history is temporary. Refreshing or starting a new chat clears it. Older messages may be omitted from long chats.</p>
    </section>
    {summary!==null&&<section className="support-panel" aria-label="Human support"><h2>Prepare a support summary</h2><p>This demo has no staffed support queue. For a real issue, use your bank’s official app or website. This draft is not sent anywhere.</p><textarea aria-label="Support summary" rows={7} value={summary} onChange={e=>{setSummary(e.target.value);setApproved(false);setCopied(false);}}/><label><input type="checkbox" checked={approved} onChange={e=>setApproved(e.target.checked)}/> I reviewed the summary and removed private details</label><div><button disabled={!approved} onClick={async()=>{try{await navigator.clipboard.writeText(redact(summary));setCopied(true);}catch{setNotice('Copy unavailable. Select and copy the draft manually.');}}}>Copy summary</button><button onClick={()=>setSummary(null)}>Close summary</button></div>{copied&&<p role="status">Copied. No ticket was sent.</p>}</section>}
    {article&&<section className="support-panel" aria-label="Source details"><h2>{article.title}</h2><p>{article.notice}</p><p>{article.content}</p><p>Publisher: {article.publisher}</p>
      {article.reference_url&&<p><a href={article.reference_url} target="_blank" rel="noopener noreferrer">Read the original source</a></p>}
      {article.checked_on&&<p>Source checked: {article.checked_on}. Review due: {article.review_due}.</p>}
      {article.current===false&&<p role="status">This source needs review and is not used for new answers.</p>}
      <small>Version {article.version}</small><button onClick={()=>setArticle(null)}>Close source</button></section>}
    <footer className="bank-chat-footer">{session?'Signed in for synthetic demo records':'Guest access — general information only'}</footer>
  </div></div>;
}
