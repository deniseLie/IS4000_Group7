import {useEffect,useRef,useState} from 'react';
import Icon from './Icon';
import {supabase} from '../lib/supabase';
import {listConversations,loadConversation} from '../lib/conversations';
import {formatDate,formatTime} from '../lib/format';
import {cacheOwner,cached,remember} from '../lib/cache';

const PREVIEWS=10;  // titles are read from the newest saved chats only, to keep opening the panel quick
function when(value) {return formatDate(value)===formatDate(new Date())?`Today, ${formatTime(value)}`:formatDate(value);}
function clip(text,size) {const line=(text||'').replace(/\s+/g,' ').trim();return line.length>size?`${line.slice(0,size-1)}…`:line;}

// Chat history column beside the assistant: the open conversation first, then earlier saved chats.
// Each title is the first question asked in that chat and the subtitle the start of the first reply (both saved redacted).
export default function SavedChats({current,closing=false,onClose,onOpen,onLoaded}) {
  // The last list shows at once while it refreshes; a chat's title (its first question and reply) never changes once
  // known, so only chats without a cached title are read again.
  const [sessions,setSessions]=useState(()=>cached('chats')),[previews,setPreviews]=useState(()=>cached('chat-titles')||{});
  const owner=useRef(cacheOwner());
  useEffect(()=>{remember('chat-titles',previews,owner.current);},[previews]);
  const [error,setError]=useState(''),[opening,setOpening]=useState(false);
  const heading=useRef(null),column=useRef(null),trigger=useRef(document.activeElement);
  const close=()=>onClose();  // the parent plays the closing motion
  useEffect(()=>{
    heading.current?.focus();
    const escape=event=>{if(event.key==='Escape')close();};
    // A press anywhere outside the column closes it too; its own Recent chats button and open dialogs keep their behaviour.
    const away=event=>{if(!column.current?.contains(event.target)&&!event.target.closest?.('[data-testid="open-recent-chats"],dialog'))close();};
    document.addEventListener('keydown',escape);document.addEventListener('pointerdown',away);
    const previous=trigger.current;
    return ()=>{document.removeEventListener('keydown',escape);document.removeEventListener('pointerdown',away);if(previous?.isConnected)previous.focus();};
  },[]);
  useEffect(()=>{
    const controller=new AbortController(),scope=cacheOwner();
    async function load() {
      try {
        const {data,error:failure}=await supabase.auth.getSession();
        if(failure)throw failure;
        if(!data.session)throw new Error('Please sign in again to open saved chats.');
        const headers={Authorization:`Bearer ${data.session.access_token}`};
        const result=await listConversations(headers,controller.signal);
        if(controller.signal.aborted)return;
        setSessions(remember('chats',result,scope));onLoaded?.(result.length);
        const titles=cached('chat-titles')||{};
        await Promise.all(result.slice(0,PREVIEWS).filter(item=>!(titles[item.session_id]?.title&&titles[item.session_id]?.detail)).map(async item=>{
          try {
            const history=await loadConversation({session_id:item.session_id,guest_token:null},headers,controller.signal);
            const asked=history.messages.find(m=>m.role==='user')?.content,reply=history.messages.find(m=>m.role==='assistant'&&!m.error)?.content;
            const preview={title:asked&&clip(asked,60),detail:reply&&clip(reply,80)};
            if(!controller.signal.aborted)setPreviews(known=>({...known,[item.session_id]:preview}));
          } catch {}  // a chat without a preview still opens; it keeps its mode label as the title
        }));
      } catch(failure) {if(failure.name!=='AbortError'&&!controller.signal.aborted)setError(failure.message);}
    }
    load();return ()=>controller.abort();
  },[]);
  const active=sessions?.filter(item=>item.session_id===current)||[],previous=sessions?.filter(item=>item.session_id!==current)||[];
  const row=item=>{
    const preview=previews[item.session_id],mode=item.mode==='public'?'General help':'My records',mine=item.session_id===current;
    return <li key={item.session_id}><button className={`saved-chat-button ${mine?'current':''}`} disabled={opening}
      data-testid="saved-chat" data-conversation-id={item.session_id} onClick={async()=>{setOpening(true);setError('');try{await onOpen(item);}catch(failure){if(failure.name!=='AbortError')setError(failure.message);}finally{setOpening(false);}}}>
      <span className="saved-chat-when"><strong>{when(item.last_active_at)}</strong>{mine?<em>Current</em>:<Icon name="chevron"/>}</span>
      <span className="saved-chat-title">{preview?.title||mode}</span>
      <span className="saved-chat-sub">{preview?.detail||`${mode} · ${item.language}`}{item.active_turn_id?' · Reply pending':''}</span></button></li>;
  };
  return <div ref={column} className={`chat-history-track ${closing?'closing':''}`}><aside className={`chat-history ${closing?'closing':''}`} aria-label="Saved chats" data-testid="saved-chats-panel">
    <div className="chat-history-head"><h2 ref={heading} tabIndex={-1}><Icon name="history"/>Past conversations</h2>
      <button className="icon-button" aria-label="Close saved chats" onClick={close}><Icon name="close"/></button></div>
    <div className="chat-history-body">
      <button className="bank-chat-logout saved-hide" onClick={close}>Hide history<Icon name="close"/></button>
      {!sessions&&!error&&<div className="loading-list" role="status" aria-label="Loading saved chats">{[0,1,2,3].map(key=><span key={key} className="skeleton-card"/>)}</div>}
      {error&&<p role="alert">{error}</p>}
      {sessions?.length===0&&<p className="saved-empty" data-testid="saved-chats-empty">No saved chats yet.</p>}
      {active.length>0&&<><h3 className="saved-group">Active session</h3><ul className="saved-chats-list">{active.map(row)}</ul></>}
      {previous.length>0&&<><h3 className="saved-group">{active.length?'Previous chats':'Recent chats'}</h3><ul className="saved-chats-list">{previous.map(row)}</ul></>}
    </div>
    <p className="chat-history-foot">Your 50 most recent chats are saved, redacted, to your demo profile. Long chats reopen at their latest messages.</p>
  </aside></div>;
}
