import {useEffect,useRef,useState} from 'react';
import Icon from '../Icon';
import {rmDelete,rmGet} from '../../lib/rm';
import {formatDate,formatTime} from '../../lib/format';

function when(value) {return formatDate(value)===formatDate(new Date())?`Today, ${formatTime(value)}`:formatDate(value);}

// Past RM assistant conversations, in the same history column as the client's Recent chats. Only Sophie's own.
export default function RmHistory({token,current,refresh,closing,onClose,onOpen}) {
  const [items,setItems]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const column=useRef(null),heading=useRef(null);
  async function load() {
    try {setItems((await rmGet('/conversations',await token())).data);setError('');}
    catch(failure) {setError(failure.message);}
  }
  useEffect(()=>{load();},[refresh]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(()=>{
    heading.current?.focus();
    const escape=event=>{if(event.key==='Escape')onClose();};
    document.addEventListener('keydown',escape);return ()=>document.removeEventListener('keydown',escape);
  },[]);  // eslint-disable-line react-hooks/exhaustive-deps
  async function remove(id) {
    setBusy(true);
    try {await rmDelete(`/conversations/${id}`,await token());await load();}catch(failure){setError(failure.message);}finally{setBusy(false);}
  }
  return <div ref={column} className={`chat-history-track ${closing?'closing':''}`}><aside className={`chat-history ${closing?'closing':''}`} aria-label="Past conversations" data-testid="rm-history">
    <div className="chat-history-head"><h2 ref={heading} tabIndex={-1}><Icon name="history"/>Past conversations</h2>
      <button className="icon-button" aria-label="Close past conversations" onClick={onClose}><Icon name="close"/></button></div>
    <div className="chat-history-body">
      {!items&&!error&&<div className="loading-list" role="status" aria-label="Loading conversations">{[0,1,2].map(key=><span key={key} className="skeleton-card"/>)}</div>}
      {error&&<p role="alert">{error}</p>}
      {items?.length===0&&<p className="saved-empty">No saved conversations yet.</p>}
      {items?.length>0&&<ul className="saved-chats-list">{items.map(item=><li key={item.conversation_id} className="rm-history-item">
        <button className={`saved-chat-button ${item.conversation_id===current?'current':''}`} disabled={busy} data-testid="rm-saved-conversation" onClick={()=>onOpen(item.conversation_id)}>
          <span className="saved-chat-when"><strong>{when(item.updated_at)}</strong>{item.conversation_id===current?<em>Current</em>:<Icon name="chevron"/>}</span>
          <span className="saved-chat-title">{item.title}</span>
          <span className="saved-chat-sub">{item.clients.length?item.clients.join(', '):'Whole book'} · {item.turns} messages</span></button>
        <button className="link-button rm-history-delete" disabled={busy} aria-label={`Delete ${item.title}`} onClick={()=>remove(item.conversation_id)}>Delete</button>
      </li>)}</ul>}
    </div>
    <p className="chat-history-foot">Your 50 most recent conversations are saved to your RM profile, redacted. Turns about clients no longer in your book are not shown again.</p>
  </aside></div>;
}
