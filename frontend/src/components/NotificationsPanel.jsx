import {useEffect,useRef,useState} from 'react';
import Panel from './Panel';
import {supabase} from '../lib/supabase';
import {loadInbox,markNotificationRead} from '../lib/notifications';

export default function NotificationsPanel({onClose}) {
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
        supabase.removeChannel(channel).then(result=>{if(result!=='ok'&&mounted.current)setLive('Live inbox disconnect could not be confirmed. Close this panel and reopen it to reconnect.');});
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
  return <Panel label="Notifications" title="Your demo inbox" closeLabel="Close notifications" onClose={onClose}>
    <p>Recorded updates for your synthetic demo profile. These messages do not change a real bank account.</p>
    <p data-testid="inbox-live-status" role="status">{live}</p>
    <button className="bank-chat-logout" data-testid="reload-notifications" disabled={busy||marking} onClick={refresh}>Reload notifications</button>
    {busy&&<p role="status">Checking recorded notifications…</p>}
    {error&&<p role="alert" data-testid="inbox-error">{error}</p>}
    {inbox?.items.length===0&&<p data-testid="inbox-empty">No notifications are recorded for your demo profile.</p>}
    <ul className="command-list" data-testid="inbox-list">{inbox?.items.map(item=><li key={item.notification_id} data-notification-id={item.notification_id} data-template-key={item.template_key}>
      <h3>{item.title}</h3><p>{item.body}</p>
      <p>{item.is_service?'Service update':'Optional update'} · <time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString()}</time></p>
      <p data-testid="notification-read-state">{item.read_at?'Read':'Unread'}</p>
      {!item.read_at&&<button className="bank-chat-logout" data-testid="mark-notification-read" disabled={busy||marking||!!error} onClick={()=>markRead(item)}>Mark as read</button>}
    </li>)}</ul>
  </Panel>;
}
