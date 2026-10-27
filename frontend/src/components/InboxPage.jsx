import {useEffect,useRef,useState} from 'react';
import Icon from './Icon';
import {supabase} from '../lib/supabase';
import {loadInbox,markNotificationRead} from '../lib/notifications';
import {formatDateTime} from '../lib/format';

const CATEGORIES={card:'Cards',booking:'Appointments',compliance:'Compliance',document:'Documents',consent:'Data sharing',agenda:'Data sharing',rm:'Relationship',request:'Requests',requests:'Requests',handoff:'Support'};
const TABS=[['all','All',()=>true],['unread','Unread',item=>!item.read_at],['service','Service updates',item=>item.is_service],['read','Read',item=>!!item.read_at]];
export function category(item) {return CATEGORIES[item.template_key.split('.')[0]]||'Update';}

// Links map a notification category to the page or panel where it can be followed up.
export default function InboxPage({links={},onActions}) {
  const [tab,setTab]=useState('all');
  const [inbox,setInbox]=useState(null);
  const [busy,setBusy]=useState(false);
  const [marking,setMarking]=useState(false);
  const [error,setError]=useState('');
  const [live,setLive]=useState('Connecting to live inbox updates…');
  const active=useRef(null);
  const acknowledgement=useRef(null);
  const subscription=useRef(null);
  const mounted=useRef(false);
  async function token() {
    const {data,error:failure}=await supabase.auth.getSession();
    if(failure)throw failure;
    if(!data.session)throw new Error('Please sign in again to view notifications.');
    return data.session.access_token;
  }
  async function refresh() {
    active.current?.abort();
    const controller=new AbortController();active.current=controller;setBusy(true);setError('');
    try {
      const result=await loadInbox(await token(),controller.signal);
      if(!controller.signal.aborted&&mounted.current){setInbox(result);return result;}
    } catch(failure) {if(failure.name!=='AbortError'&&mounted.current){setError(failure.message);setInbox(null);}}
    finally {if(!controller.signal.aborted&&mounted.current)setBusy(false);}
    return null;
  }
  async function subscribe(customerId) {
    await supabase.realtime.setAuth(await token());
    if(!mounted.current)return;
    const channel=supabase.channel(`customer-inbox-${customerId}-${crypto.randomUUID()}`)
      .on('postgres_changes',{event:'INSERT',schema:'notify',table:'inbox',filter:`customer_id=eq.${customerId}`},()=>refresh())
      .on('postgres_changes',{event:'UPDATE',schema:'notify',table:'inbox',filter:`customer_id=eq.${customerId}`},()=>refresh());
    subscription.current=channel;
    channel.subscribe(status=>{
      if(!mounted.current||subscription.current!==channel)return;
      if(status==='SUBSCRIBED'){setLive('Live inbox updates connected.');refresh();}
      if(['CHANNEL_ERROR','TIMED_OUT','CLOSED'].includes(status)) {
        setLive(`Live inbox updates are unavailable (${status}). Use Reload notifications to check recorded messages.`);
        subscription.current=null;
        supabase.removeChannel(channel).then(result=>{if(result!=='ok'&&mounted.current)setLive('Live inbox disconnect could not be confirmed. Leave this page and reopen Inbox to reconnect.');});
      }
    });
  }
  useEffect(()=>{
    mounted.current=true;
    async function open() {
      const result=await refresh();
      if(!result||!mounted.current)return;
      try {await subscribe(result.customer_id);}
      catch(failure) {if(mounted.current)setLive(`Live inbox updates could not connect: ${failure.message}`);}
    }
    open();
    return ()=>{mounted.current=false;active.current?.abort();acknowledgement.current?.abort();const channel=subscription.current;subscription.current=null;if(channel)supabase.removeChannel(channel);};
  },[]);
  async function markRead(item) {
    const controller=new AbortController();acknowledgement.current=controller;setMarking(true);setError('');
    try {
      await markNotificationRead(item.notification_id,inbox.customer_id,await token(),controller.signal);
      if(!controller.signal.aborted&&mounted.current)await refresh();
    } catch(failure) {if(failure.name!=='AbortError'&&mounted.current)setError(failure.message);}
    finally {if(!controller.signal.aborted&&mounted.current){acknowledgement.current=null;setMarking(false);}}
  }
  const items=inbox?.items||[];
  const visible=items.filter(TABS.find(entry=>entry[0]===tab)[2]);
  const connected=live==='Live inbox updates connected.';
  return <section className="app-page-body inbox-page ib" aria-label="Notifications" data-testid="inbox-page">
    <div className="page-header"><div><p className="ib-eyebrow"><span>Notifications</span><span className={`ib-live ${connected?'on':''}`} data-testid="inbox-live-status" role="status"><span className="dot"/>{live}</span></p><h1>Inbox</h1><p>Updates for your account in this demo. These messages do not change a real bank account.</p></div>
      <div className="page-header-actions"><button className="bank-chat-logout" data-testid="reload-notifications" disabled={busy||marking} onClick={refresh}><Icon name="refresh"/>Reload notifications</button>{onActions&&<button className="bank-chat-logout" onClick={onActions}><Icon name="document"/>Actions</button>}</div></div>
    <div className="ib-tabs" role="tablist" aria-label="Filter notifications">{TABS.map(([key,label,match])=><button key={key} role="tab" aria-selected={tab===key} className={tab===key?'active':''} onClick={()=>setTab(key)}>{label} ({items.filter(match).length})</button>)}</div>
    {busy&&<p className="ib-busy loading" role="status">Checking recorded notifications…</p>}
    {busy&&!inbox&&<div className="loading-list" aria-hidden="true">{[0,1,2].map(key=><span key={key} className="skeleton-card large"/>)}</div>}
    {error&&<p role="alert" data-testid="inbox-error">{error}</p>}
    {inbox?.items.length===0&&<p className="page-empty" data-testid="inbox-empty">No notifications are recorded for your account.</p>}
    {inbox&&items.length>0&&visible.length===0&&<p className="page-empty">No notifications in this view.</p>}
    <ul className="notification-list ib-list" data-testid="inbox-list">{visible.map(item=>{
      const label=category(item),link=links[label];
      return <li key={item.notification_id} className={item.read_at?'':'unread'} data-notification-id={item.notification_id} data-template-key={item.template_key}>
        <div className="ib-head"><span className="category-badge">{label}</span><span className="ib-state">{!item.read_at&&<span className="dot"/>}{item.is_service?'Service update':'Optional update'} · <span data-testid="notification-read-state">{item.read_at?'Read':'Unread'}</span></span>
          <time dateTime={item.created_at}>{formatDateTime(item.created_at)}</time></div>
        <h3>{item.title}</h3>
        <p>{item.body}</p>
        <div className="ib-foot"><code>Ref #{item.notification_id.slice(0,8).toUpperCase()}</code>
          <div className="panel-actions">{!item.read_at&&<button className="bank-chat-logout" data-testid="mark-notification-read" disabled={busy||marking||!!error} onClick={()=>markRead(item)}>Mark as read</button>}{link&&<button className={`ib-link ${item.read_at?'':'strong'}`} onClick={link.onSelect}>{link.label} →</button>}</div></div>
      </li>;})}</ul>
  </section>;
}
