import {useEffect,useRef,useState} from 'react';
import Icon from './components/Icon';
import Sidebar,{TabBar} from './components/Sidebar';
import RmAssistant from './components/rm/RmAssistant';
import RmHistory from './components/rm/RmHistory';
import ClientsPage from './components/rm/ClientsPage';
import RmTicketsPage from './components/rm/RmTicketsPage';
import SchedulePage from './components/rm/SchedulePage';
import FollowUpsPage from './components/rm/FollowUpsPage';
import TodayPage from './components/rm/TodayPage';
import QualityPage from './components/QualityPage';
import {supabase} from './lib/supabase';
import {display,newId,restoreConversation,rmGet} from './lib/rm';

const API_URL=import.meta.env.VITE_API_URL||'/api';
const PAGES=['today','assistant','clients','tickets','schedule','followups','quality'];
// Today is the start page (no hash); every other page has its own #rm-… address.
function pageFromHash() {const page=location.hash.replace(/^#rm-?/,'').replace(/^#/,'');return PAGES.includes(page)?page:'today';}
const EMPTY={client_id:null,ticket_ref:null,appointment_id:null,note_id:null,draft:null,documentation:null};

// Persona 2: the relationship manager's workspace. Same shell, type and buttons as the client app; RM pages instead of
// the client's. The assistant keeps its conversation and active client while Sophie moves between pages.
export default function RmWorkspace({session}) {
  const [view,setView]=useState(pageFromHash);
  const [profile,setProfile]=useState(null),[profileError,setProfileError]=useState('');
  const [messages,setMessages]=useState([]);
  const [context,setContext]=useState(EMPTY);
  const [conversationId,setConversationId]=useState(newId);  // each conversation is saved under its own id (0091)
  const [historyOpen,setHistoryOpen]=useState(false),[historyKey,setHistoryKey]=useState(0),[notice,setNotice]=useState('');
  const [ask,setAsk]=useState(null);  // a question started from another page, picked up by the assistant
  const [chatCount,setChatCount]=useState(null),[ticketCount,setTicketCount]=useState(0);
  const [pageKey,setPageKey]=useState(0);
  const tokenRef=useRef(null);

  async function token() {
    const {data,error}=await supabase.auth.getSession();
    if(error)throw error;
    if(!data.session)throw new Error('Session expired. Please sign in again.');
    tokenRef.current=data.session.access_token;
    return data.session.access_token;
  }
  useEffect(()=>{
    const controller=new AbortController();
    (async()=>{try{setProfile(await rmGet('/me',await token(),controller.signal));}
      catch(failure){if(failure.name!=='AbortError')setProfileError(failure.message);}})();
    return ()=>controller.abort();
  },[session.user.id]);
  // Counts shown like the client app: Recent chats (saved conversations) and requests that need Sophie's action.
  useEffect(()=>{(async()=>{try{setChatCount((await rmGet('/conversations',await token())).data.length);}catch{}})();},[historyKey]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(()=>{(async()=>{try{setTicketCount(((await rmGet('/tickets',await token())).data.rm_owned||[]).length);}catch{}})();},[view]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(()=>{const sync=()=>setView(pageFromHash());window.addEventListener('hashchange',sync);return ()=>window.removeEventListener('hashchange',sync);},[]);

  function navigate(page) {
    if(page===view){setPageKey(k=>k+1);return;}
    location.hash=page==='today'?'':`rm-${page}`;
    if(page==='today')history.replaceState(null,'',location.pathname+location.search);
    setView(page);
  }
  // From any page: open the assistant with a question for a client, ticket, appointment or note.
  function askAssistant(message,options={}) {
    setAsk({message,intent:options.intent||null,context:options.context||{},prefill:!!options.prefill,key:Date.now()});
    navigate('assistant');
  }
  function newConversation() {setMessages([]);setContext(EMPTY);setConversationId(newId());setNotice('');}
  async function openConversation(id) {
    try {
      const saved=await rmGet(`/conversations/${id}`,await token());
      const restored=restoreConversation(saved);
      setMessages(restored.messages);setContext(restored.context);setConversationId(id);setHistoryOpen(false);navigate('assistant');
      setNotice(saved.withheld_turns?`${saved.withheld_turns} earlier message(s) about clients no longer in your book are not shown.`:'Conversation reopened. Earlier drafts and reviews are read-only; ask again to continue.');
    } catch(failure) {setNotice(failure.message);}
  }
  async function signOut() {
    try{const {data}=await supabase.auth.getSession();if(data.session)await fetch(`${API_URL}/auth/session-end`,{method:'POST',headers:{Authorization:`Bearer ${data.session.access_token}`}});}catch{}
    history.replaceState(null,'',location.pathname+location.search);
    await supabase.auth.signOut();
  }
  const name=profile?display(profile.advisor.name):profileError?'Relationship manager':null;
  const pages=[
    {view:'today',testid:'rm-open-today',icon:'sun',label:'Today',short:'Today'},
    {view:'assistant',testid:'rm-open-assistant',icon:'spark',label:'RM assistant',short:'Assistant'},
    {view:'clients',testid:'rm-open-clients',icon:'people',label:'My clients',short:'Clients'},
    {view:'tickets',testid:'rm-open-tickets',icon:'clip',label:'Ticket inbox',short:'Tickets',badge:ticketCount,badgeLabel:'need your action'},
    {view:'schedule',testid:'rm-open-schedule',icon:'calendar',label:'Schedule',short:'Schedule'},
    {view:'followups',testid:'rm-open-followups',icon:'bell',label:'Follow-ups',short:'Follow-ups'},
    {view:'quality',testid:'rm-open-quality',icon:'shield',label:'Quality',short:'Quality'},
  ].map(page=>({...page,onSelect:()=>navigate(page.view)}));
  const menu=[{testid:'sign-out',icon:'logout',label:'Sign out',danger:true,onSelect:signOut}];
  const footer=<footer className="bank-chat-footer"><span>IS4000 · Coursework demo · Synthetic data · No real banking services</span></footer>;
  const shared={token,onAsk:askAssistant,today:profile?.today};

  return <div className="bank-chat-page rmw-shell" data-testid="rmw-shell">
    <a className="skip-link" href="#rm-composer">Skip to message</a>
    <header className="bank-chat-header">
      <div className="bank-chat-brand"><span className="brand-name">DemoBank<span className="brand-dot">.</span></span>
        <div className="brand-caption"><strong>Relationship manager workspace</strong><span className="demo-badge">Fictional demo data</span></div></div>
      <div className="chat-actions">
        {profile&&<span className="rm-header-meta" data-testid="rm-book-size"><Icon name="people"/>{profile.book_size} clients in your book</span>}
        <span className="rm-role-badge" title="Signed in with the relationship manager role">RM · {profile?.advisor.team||'Signed in'}</span>
      </div>
    </header>
    <div className="bank-chat-layout">
      <Sidebar session={session} name={name} subtitle={profile?`${profile.advisor.role} · signed in`:'Relationship manager · signed in'} menu={menu} current={view} pages={pages} onSignOut={signOut}/>
      <TabBar current={view} pages={pages}/>
      <main className="chat-workspace" hidden={view!=='assistant'}>
        <RmAssistant {...shared} messages={messages} setMessages={setMessages} context={context} setContext={setContext} ask={ask} onAsked={()=>setAsk(null)}
          conversationId={conversationId} onNew={newConversation} onSaved={()=>setHistoryKey(k=>k+1)} chatCount={chatCount} historyOpen={historyOpen} onHistory={()=>setHistoryOpen(o=>!o)} notice={notice} onNotice={setNotice}
          advisor={profile?.advisor} onNavigate={navigate}/>
        {footer}
      </main>
      {view==='assistant'&&historyOpen&&<RmHistory token={token} current={conversationId} refresh={historyKey} onClose={()=>setHistoryOpen(false)} onOpen={openConversation}/>}
      {view!=='assistant'&&<main className="app-page page-enter" key={`${view}-${pageKey}`}>
        {profileError&&<p className="chat-notice" role="alert">{profileError}</p>}
        {view==='clients'&&<ClientsPage {...shared}/>}
        {view==='tickets'&&<RmTicketsPage {...shared}/>}
        {view==='schedule'&&<SchedulePage {...shared}/>}
        {view==='followups'&&<FollowUpsPage {...shared}/>}
        {view==='today'&&<TodayPage {...shared} advisor={profile?.advisor}/>}
        {view==='quality'&&<QualityPage/>}
        {footer}
      </main>}
    </div>
  </div>;
}

