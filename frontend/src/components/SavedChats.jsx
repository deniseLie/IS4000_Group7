import {useEffect,useState} from 'react';
import Panel from './Panel';
import {supabase} from '../lib/supabase';
import {listConversations} from '../lib/conversations';

export default function SavedChats({onClose,onOpen}) {
  const [sessions,setSessions]=useState(null);
  const [error,setError]=useState('');
  const [opening,setOpening]=useState(false);
  useEffect(()=>{
    const controller=new AbortController();
    async function load() {
      try {
        const {data,error:failure}=await supabase.auth.getSession();
        if(failure)throw failure;
        if(!data.session)throw new Error('Please sign in again to open saved chats.');
        const result=await listConversations({Authorization:`Bearer ${data.session.access_token}`},controller.signal);
        if(!controller.signal.aborted)setSessions(result);
      } catch(failure) {if(failure.name!=='AbortError'&&!controller.signal.aborted)setError(failure.message);}
    }
    load();return ()=>controller.abort();
  },[]);
  return <Panel label="Saved chats" title="Your recent chats" closeLabel="Close saved chats" onClose={onClose}>
    <p>Reopen one of your 50 most recent conversations. Long chats show their latest messages.</p>
    {!sessions&&!error&&<p role="status">Loading saved chats…</p>}
    {error&&<p role="alert">{error}</p>}
    {sessions?.length===0&&<p data-testid="saved-chats-empty">No saved chats yet.</p>}
    <ul className="saved-chats-list">{sessions?.map(item=><li key={item.session_id}><button className="saved-chat-button" disabled={opening}
      data-testid="saved-chat" data-conversation-id={item.session_id} onClick={async()=>{setOpening(true);setError('');try{await onOpen(item);}catch(failure){if(failure.name!=='AbortError')setError(failure.message);}finally{setOpening(false);}}}>
      <strong>{new Date(item.last_active_at).toLocaleString()}</strong><span>{item.mode==='public'?'General help':'My records'} · {item.language}</span>
      {item.active_turn_id&&<span>Reply pending</span>}</button></li>)}</ul>
  </Panel>;
}
