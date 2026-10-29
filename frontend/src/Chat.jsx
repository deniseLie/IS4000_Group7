import { useEffect, useRef, useState } from 'react';
import Icon from './components/Icon';
import Panel from './components/Panel';
import Answer from './components/Answer';
import TicketsPage from './components/TicketsPage';
import {ticketIntent} from './lib/ticketData';
import CompliancePage from './components/CompliancePage';
import OverviewPage from './components/OverviewPage';
import {displayName,firstName} from './lib/format';
import {cacheOwner,cached,remember} from './lib/cache';
import {loadSections} from './lib/customerData';
import {loadInbox} from './lib/notifications';
import SavedChats from './components/SavedChats';
import ActionsPanel from './components/ActionsPanel';
import InboxPage from './components/InboxPage';
import RecordsPanel from './components/RecordsPanel';
import CardBlockDialog from './components/CardBlockDialog';
import DisputeDialog from './components/DisputeDialog';
import ComplianceWorkspace from './components/ComplianceWorkspace';
import {loadIntroduction,relationshipIntent} from './lib/relationshipWorkflow';
import {prepareComplianceSupport} from './lib/compliance';
import TopBar from './components/TopBar';
import Sidebar,{TabBar} from './components/Sidebar';
import LockedPage from './components/LockedPage';
import { supabase } from './lib/supabase';
import { boundedHistory, handoffSummary, readEvents, recallStep, redact, sentHistory, supportSummary } from './lib/chatTools';
import {capabilities,clearActive,listConversations,createConversation,loadConversation,readActive,writeActive,newCustomerAccess,newGuestAccess,parseMetadata,cancelConversationTurn} from './lib/conversations';
import {loadWorkflowPreview,parsePausedWorkflow,parsePreview} from './lib/workflows';
import {createActionCase,proposeAction} from './lib/actions';

const API_URL = import.meta.env.VITE_API_URL || '/api';
const GUEST_SESSION = globalThis.crypto?.randomUUID?.();  // needs a secure context (https or localhost); correlation id for guest requests; signed-in sessions use the token's session_id
const ERRORS = {
  conversation_conflict:'This chat changed in another request. Reload the saved chat before retrying.',
  conversation_busy:'A reply is still pending. Reload this chat to check it or cancel the pending reply.',
  conversation_not_found:'This saved chat was not found or has expired. Start a new chat.',
  conversation_unavailable:'Saved chats are unavailable. Please retry.',
  auth_invalid:'Session expired. Please sign in again.', auth_expired:'Session expired. Please sign in again.', auth_required:'Please sign in to use your account.',
  profile_missing:'No demo customer profile is linked to this login. Sign out to chat as a guest, or ask the project owner to link a demo profile.',
  personal_unavailable:'Your records are unavailable right now. Please retry later.',
  rate_limited:'Too many messages in a short time. Please wait a moment and retry.', payload_too_large:'This conversation is too long to send. Start a new chat and try again.',
};
const PAGES=['chat','overview','inbox','compliance','requests','actions'];  // one client view: the RM pages are not shown; the assistant hands questions to the RM
const HELP_TOPICS=[['general','General banking question'],['fraud','Fraud or an unauthorised charge'],['card','Card problem'],['records','Account or records'],['compliance','Compliance review'],['relationship','Advice, fees or something else for my relationship manager']];
// Guests can open every page's address too; they see a sign-in prompt instead of records (Actions is a drawer once signed in).
function pageFromHash(session) {const page=location.hash.slice(1);return PAGES.includes(page)||!session&&page==='actions'?page:'chat';}
// The header badge says the data is fictional and each answer carries its own "General info, not advice" note.
const DISCLAIMER = 'Demo AI assistant. AI answers can contain errors. Never share your PIN, password or OTP.';

export default function Chat({session,onSignIn}) {
  const [messages,setMessages]=useState([]);
  const [input,setInput]=useState('');
  const [loading,setLoading]=useState(false);
  const [mode,setMode]=useState(session ? 'personal':'public');
  const [language,setLanguage]=useState('English');
  const [notice,setNoticeText]=useState(''),[noticeKey,setNoticeKey]=useState(0),[noticeToast,setNoticeToast]=useState(false);
  // Routine confirmations show as a small pill that folds away; anything else stays until dismissed.
  const setNotice=(text,toast=false)=>{setNoticeText(text);setNoticeToast(toast);setNoticeKey(key=>key+1);};
  const [summary,setSummary]=useState(null);
  const [approved,setApproved]=useState(false);
  const [copied,setCopied]=useState(false);
  const [copyError,setCopyError]=useState('');
  const [article,setArticle]=useState(null);
  const [store,setStore]=useState(null);
  const [booting,setBooting]=useState(true);
  const [bootError,setBootError]=useState('');
  const [conversation,setConversation]=useState(null);
  const [pending,setPending]=useState(null);
  const [workflow,setWorkflow]=useState(null);
  const [savedChats,setSavedChats]=useState(false),[historyClosing,setHistoryClosing]=useState(false);
  const historyTimer=useRef(null);
  // The history column narrows to nothing before it unmounts, so the chat widens at the same pace.
  function closeHistory() {
    if(!savedChats||historyTimer.current)return;
    if(window.matchMedia('(prefers-reduced-motion: reduce)').matches){setSavedChats(false);return;}
    setHistoryClosing(true);
    historyTimer.current=setTimeout(()=>{historyTimer.current=null;setHistoryClosing(false);setSavedChats(false);},260);
  }
  const [recordsOpen,setRecordsOpen]=useState(false);
  const [complianceOpen,setComplianceOpen]=useState(null);
  const [complianceBusy,setComplianceBusy]=useState(false);
  const [complianceReceipt,setComplianceReceipt]=useState(null);
  const [complianceReview,setComplianceReview]=useState(null),[supportScope,setSupportScope]=useState(null);
  const [blockAction,setBlockAction]=useState(null);
  const [rmIntro,setRmIntro]=useState(null),[profileName,setProfileName]=useState(session?null:'');  // null while the name is loading
  const [handoff,setHandoff]=useState({saving:false,outcome:null,error:''});
  const [helpTopic,setHelpTopic]=useState('general'),[attachSummary,setAttachSummary]=useState(true);
  const [unread,setUnread]=useState(0),[chatCount,setChatCount]=useState(null);
  const [view,setView]=useState(()=>pageFromHash(session));
  const [pageKey,setPageKey]=useState(0);
  const [ticketPrefill,setTicketPrefill]=useState(null);  // category and summary carried from a chat question to My requests
  const [overviewFocus,setOverviewFocus]=useState(null);  // section to scroll to when Overview opens from the assistant
  const messageList=useRef(null);
  const composer=useRef(null);
  const recall=useRef(null);
  const request=useRef(null);
  const version=useRef(0);
  useEffect(()=>{
    const list=messageList.current;
    if(messages.length&&list)list.scrollTo({top:list.scrollHeight,behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});
  },[messages]);
  useEffect(()=>()=>request.current?.abort(),[]);
  useEffect(()=>{  // the message box grows with its text, up to about six lines, then scrolls
    const box=composer.current;if(!box)return;
    box.style.height='auto';box.style.height=`${Math.min(box.scrollHeight,160)}px`;
  },[input]);

  const lastView=useRef(view);
  useEffect(()=>{  // leaving Actions after a card decision refreshes the saved reply it belongs to
    if(lastView.current==='actions'&&view!=='actions'&&workflow&&conversation&&!loading)reopen(conversation).catch(failure=>{if(failure.name!=='AbortError')setNotice(failure.message);});
    lastView.current=view;
  },[view]);
  useEffect(()=>{  // pages are hash-addressed so Back/Forward work; the chat stays mounted underneath
    const sync=()=>setView(pageFromHash(session));
    window.addEventListener('hashchange',sync);return ()=>window.removeEventListener('hashchange',sync);
  },[]);
  useEffect(()=>{
    const controller=new AbortController();
    async function open() {
      try {
        const available=await capabilities(controller.signal);
        if(controller.signal.aborted)return;
        setStore(available);
        if(available==='database') {
          const access=readActive(session?.user.id||null);
          if(access) {
            const history=await loadConversation(access,await sessionHeaders(),controller.signal);
            const preview=await waitingPreview(history,controller.signal);
            if(!controller.signal.aborted)restore(history,access,preview);
          }
        }
      } catch(failure) {if(failure.name!=='AbortError'&&!controller.signal.aborted)setBootError(failure.message);}
      finally {if(!controller.signal.aborted)setBooting(false);}
    }
    open();return ()=>controller.abort();
  },[]);

  useEffect(()=>{  // the relationship manager's name for Talk to a person; questions reach them as handovers
    if(!session){setRmIntro(null);return;}
    const controller=new AbortController();
    (async()=>{try{const headers=await sessionHeaders();const intro=await loadIntroduction(headers.Authorization.replace(/^Bearer /,''),controller.signal);if(!controller.signal.aborted)setRmIntro(intro);}
      catch(failure){if(failure.name!=='AbortError'&&!controller.signal.aborted)setRmIntro(null);}})();
    return ()=>controller.abort();
  },[session?.user.id]);

  useEffect(()=>{  // the signed-in customer's own name labels the account area
    if(!session){setProfileName('');return;}
    const controller=new AbortController();
    (async()=>{try{const headers=await sessionHeaders();const {profile}=await loadSections(headers.Authorization.replace(/^Bearer /,''),['profile'],controller.signal);if(!controller.signal.aborted)setProfileName(displayName(profile.data.name));}catch{if(!controller.signal.aborted)setProfileName('');}})();
    return ()=>controller.abort();
  },[session?.user.id]);

  useEffect(()=>{  // the Inbox button carries a dot while recorded notifications are unread
    if(!session){setUnread(0);return;}
    const controller=new AbortController();
    const known=cached('inbox');if(known)setUnread(known.items.filter(item=>!item.read_at).length);
    (async()=>{try{const scope=cacheOwner(),headers=await sessionHeaders();const inbox=await loadInbox(headers.Authorization.replace(/^Bearer /,''),controller.signal);if(!controller.signal.aborted)setUnread(remember('inbox',inbox,scope).items.filter(item=>!item.read_at).length);}catch{}})();
    return ()=>controller.abort();
  },[session?.user.id,view,pageKey]);

  useEffect(()=>{  // count shown on the Recent chats button
    if(!session||store!=='database'){setChatCount(null);return;}
    const controller=new AbortController();
    (async()=>{try{const list=await listConversations(await sessionHeaders(),controller.signal);if(!controller.signal.aborted)setChatCount(list.length);}catch{}})();
    return ()=>controller.abort();
  },[session?.user.id,store,conversation?.session_id]);

  async function sessionHeaders() {
    if(!session)return {};
    const {data,error}=await supabase.auth.getSession();
    if(error)throw error;
    if(!data.session)throw new Error('Session expired. Please sign in again.');
    return {Authorization:`Bearer ${data.session.access_token}`};
  }

  // A reply waiting on card decisions is rebuilt from its saved checkpoint; without it, Actions still works.
  async function waitingPreview(history,signal) {
    if(history.workflow?.state!=='awaiting_confirmation')return null;
    try {return (await loadWorkflowPreview(history.workflow.turn_id,await sessionHeaders(),signal)).preview;}
    catch(failure) {if(failure.name==='AbortError')throw failure;return null;}
  }

  function restore(history,access,preview=null) {
    writeActive(session?.user.id||null,access);
    setConversation({...history.session,guest_token:access.guest_token});setPending(history.active_turn_id);
    setWorkflow(history.workflow);
    setMessages(preview?[...history.messages,{role:'assistant',content:preview.answer,...preview}]:history.messages);setMode(history.session.mode);setLanguage(history.session.language);setInput('');setBootError('');
    setSummary(null);setArticle(null);closeHistory();setRecordsOpen(false);
    setNotice(preview?'This saved reply is waiting for your card decisions in the urgent section.':history.workflow?'This saved reply is waiting on its recorded action workflow. Open Actions to review or resume it.':history.history_trimmed?'Opened the latest saved messages. Older messages are omitted from this long chat.':'Saved chat reopened.',!preview&&!history.workflow&&!history.history_trimmed);
  }

  async function reopen(item) {
    version.current++;request.current?.abort();
    const epoch=version.current,controller=new AbortController();request.current=controller;setLoading(true);
    const access={session_id:item.session_id,guest_token:conversation?.session_id===item.session_id?conversation.guest_token:null};
    try {
      const history=await loadConversation(access,await sessionHeaders(),controller.signal);
      const preview=await waitingPreview(history,controller.signal);
      if(epoch===version.current)restore(history,access,preview);
    } finally {if(epoch===version.current){setLoading(false);request.current=null;}}
  }

  async function cancelPending() {
    version.current++;request.current?.abort();
    const epoch=version.current,controller=new AbortController();request.current=controller;setLoading(true);
    try {
      const history=await cancelConversationTurn(conversation,pending,await sessionHeaders(),controller.signal);
      if(epoch===version.current)restore(history,conversation);
    } catch(failure) {if(failure.name!=='AbortError'&&epoch===version.current)setNotice(failure.message);}
    finally {if(epoch===version.current){setLoading(false);request.current=null;}}
  }

  function reset() {
    if(store==='database') {
      try {clearActive(session?.user.id||null);}
      catch(failure) {setBootError(failure.message);return false;}
    }
    version.current++;request.current?.abort();request.current=null;
    setMessages([]);setInput('');setLoading(false);setMode(session?'personal':'public');setNotice('New chat started.',true);
    setSummary(null);setApproved(false);setCopied(false);setCopyError('');setArticle(null);
    setConversation(null);setPending(null);setWorkflow(null);setSavedChats(false);setRecordsOpen(false);setComplianceOpen(null);setSupportScope(null);if(store!==null)setBootError('');
    return true;
  }

  async function endSession() {  // records auth.session_ended; sign-out goes ahead even if this fails
    try {
      const {data}=await supabase.auth.getSession();
      if(data.session)await fetch(`${API_URL}/auth/session-end`,{method:'POST',headers:{Authorization:`Bearer ${data.session.access_token}`}});
    } catch {}
  }

  async function send(e,supplied=null,recordContext=null) {
    e?.preventDefault();
    const question=(supplied??input).trim();if(!question||loading||booting||bootError||pending||(e&&complianceBusy))return;
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
      Object.assign(headers,await sessionHeaders());
      let thread=conversation;
      if(store==='database'&&!thread) {
        const access=session?newCustomerAccess():newGuestAccess();
        const created=await createConversation(access,mode,language,headers,controller.signal);
        if(epoch!==version.current)return;
        thread={...created,guest_token:access.guest_token};writeActive(session?.user.id||null,access);setConversation(thread);
      }
      if(thread?.guest_token)headers['X-Conversation-Token']=thread.guest_token;
      const stored=store==='database'?{conversation_id:thread.session_id,turn_id:crypto.randomUUID(),revision:thread.revision}:{};
      const response=await fetch(`${API_URL}/chat`,{method:'POST',headers,body:JSON.stringify({messages:history,mode,language,...stored,...(recordContext&&{record_context:recordContext})}),signal:controller.signal});
      if(!response.ok) {
        const code=await response.json().then(b=>b?.error?.code,()=>null);
        throw new Error(ERRORS[code]||'The banking service is unavailable. Please retry.');
      }
      if(!response.body)throw new Error('No response received.');
      await readEvents(response.body,(type,data)=>{
        if(epoch!==version.current)return;
        if(type==='conversation') {
          const metadata=parseMetadata(JSON.parse(data));
          if(metadata.conversation_id!==thread?.session_id||metadata.revision<thread.revision)throw new Error('The saved reply does not match this chat. Reload it before retrying.');
          setConversation({...thread,revision:metadata.revision,active_turn_id:metadata.active_turn_id});setPending(metadata.active_turn_id);
        }
        if(type==='answer') {
          const result=JSON.parse(data);
          setMessages([...visible,{role:'assistant',content:result.answer,...result}]);
        }
        if(type==='workflow_paused') {
          const body=JSON.parse(data);
          const paused=parsePausedWorkflow(body,thread.session_id);
          if(paused.turn_id!==stored.turn_id)throw new Error('The proposed actions do not match this reply. Reload the saved chat.');
          const preview=parsePreview(body.preview,paused);
          setWorkflow(paused);setMessages([...visible,{role:'assistant',content:preview.answer,...preview}]);setNotice('Your card decisions are waiting in the urgent section.');
        }
      });
    } catch(err) {
      if(err.name!=='AbortError'&&epoch===version.current)setMessages([...visible,{role:'assistant',content:err.message,error:true}]);
    } finally {
      if(epoch===version.current){setLoading(false);request.current=null;}
    }
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

  function navigate(page) {
    setOverviewFocus(null);
    if(page===view){if(page!=='chat')setPageKey(key=>key+1);return;}  // choosing the open page reloads it
    if(page==='chat')history.pushState(null,'',location.pathname+location.search);else location.hash=page;
    setView(page);
  }
  function draft(question) { setInput(question);requestAnimationFrame(()=>composer.current?.focus()); }
  function recallSent(e) {  // Up/Down in an empty box (or one still showing a recalled question) steps through sent questions
    const up=e.key==='ArrowUp';if((!up&&e.key!=='ArrowDown')||e.altKey||e.ctrlKey||e.metaKey||e.shiftKey||e.nativeEvent.isComposing)return;
    const box=e.currentTarget,sent=sentHistory(messages),browsing=recall.current!==null&&input===sent[recall.current];
    if(!sent.length||(input&&!browsing))return;
    if(browsing&&(up?input.lastIndexOf('\n',box.selectionStart-1)>=0:input.indexOf('\n',box.selectionEnd)>=0))return;  // move within a multi-line question first
    e.preventDefault();recall.current=recallStep(sent.length,browsing?recall.current:null,up);
    const text=recall.current===null?'':sent[recall.current];setInput(text);
    requestAnimationFrame(()=>composer.current?.setSelectionRange(text.length,text.length));
  }
  async function prepareReviewSupport(selection){
    setSupportScope(null);setAttachSummary(true);setHelpTopic('compliance');setHandoff({saving:true,outcome:null,error:''});
    try{const headers=await sessionHeaders();const token=headers.Authorization?.replace(/^Bearer /,'');
      const prepared=await prepareComplianceSupport(selection,token);setSupportScope(prepared.case_id);setSummary(prepared.summary);setHandoff({saving:false,outcome:null,error:''});
    }catch(error){setNotice(error.message);setHandoff({saving:false,outcome:null,error:error.message});}
  }
  function prepareSummary() { if(session&&(view==='compliance'||view==='chat'&&showCompliance)&&complianceReview){prepareReviewSupport({review_case_id:complianceReview});return;} setSupportScope(null);setAttachSummary(true);setSummary(session?handoffSummary(messages):supportSummary(messages));setApproved(false);setCopied(false);setCopyError('');setHandoff({saving:false,outcome:null,error:''});setHelpTopic('general'); }
  async function sendHandoff() {  // signed in: records a real support handoff (an automatic action) from the label-only summary
    setHandoff({saving:true,outcome:null,error:''});
    try {
      const {data,error}=await supabase.auth.getSession();
      if(error)throw error;
      if(!data.session)throw new Error('Session expired. Please sign in again.');
      const token=data.session.access_token;
      const scope=supportScope?{case_id:supportScope}:await createActionCase({session_id:crypto.randomUUID(),case_id:crypto.randomUUID()},token);
      const urgent=messages.some(m=>m.sections?.some(section=>section.priority==='P0'));
      const saved=await proposeAction({command_id:crypto.randomUUID(),task_id:'handoff',case_id:scope.case_id,type:'handoff.create',
        target:{reason:'general_support',priority:urgent?'P0':'P2',summary:handoffText}},token);
      if(saved.receipt?.outcome!=='succeeded')throw new Error('Your question could not be sent. Please try again.');
      setHandoff({saving:false,outcome:saved,error:''});
      setNotice('Sent to your relationship manager. You can follow it in My requests.');
    } catch(failure) {setHandoff({saving:false,outcome:null,error:failure.message});}
  }
  const topicLabel=HELP_TOPICS.find(entry=>entry[0]===helpTopic)[1];
  const handoffText=summary===null?null:supportScope?summary:`${attachSummary?summary:'Customer asked for a person.'} Requested help: ${topicLabel}.`;  // fixed labels only
  const topics=[
    {icon:'account',title:'Accounts & savings',detail:'Make sense of complex banking needs.',question:'What happens if I withdraw my fixed deposit early?'},
    {icon:'card',title:'Cards & payments',detail:'Understand payments and charges.',question:'Does paying only the minimum on my credit card avoid interest?'},
    {icon:'loan',title:'Borrowing basics',detail:'Explore the different types of loans.',question:'What is the difference between a secured and unsecured loan?'},
  ];

  function requestStatement(summary) {setTicketPrefill({category:'statement_request',summary,key:Date.now()});navigate('requests');}
  // Request categories (CL1/TK1/TK2): a matching question, or a reply handed to the RM, ends with a button to My requests.
  function ticketStep(message,index) {
    if(message.role!=='assistant'||!message.content||!session||mode!=='personal')return null;
    const asked=messages[index-1]?.role==='user'?messages[index-1].content:'';const intent=ticketIntent(asked);
    if(intent==='status')return <button className="primary-button rm-handoff" data-testid="ticket-handoff" onClick={()=>{setTicketPrefill(null);navigate('requests');}}>See my requests<Icon name="arrow"/></button>;
    if(intent==='card_limit_change')return <button className="primary-button rm-handoff" data-testid="ticket-handoff" onClick={()=>{setTicketPrefill({category:intent,summary:redact(asked).slice(0,500),key:Date.now()});navigate('requests');}}>Raise a card limit request<Icon name="arrow"/></button>;
    // CR15: a statement question ends with a way to the Statements section, or to a statement copy request for older ones.
    if(intent==='statements')return <button className="primary-button rm-handoff" data-testid="statement-handoff" onClick={()=>{navigate('overview');setOverviewFocus({section:'statements',key:Date.now()});}}>Go to Statements<Icon name="arrow"/></button>;
    if(intent==='statement_request')return <button className="primary-button rm-handoff" data-testid="ticket-handoff" onClick={()=>requestStatement(redact(asked).slice(0,500))}>Request a statement copy<Icon name="arrow"/></button>;
    // CR18: a meeting question ends with a button to the Meetings tab, where she picks a free time.
    if(relationshipIntent(asked)==='meeting')return <button className="primary-button rm-handoff" data-testid="meeting-handoff" onClick={()=>{setTicketPrefill({tab:'meetings',key:Date.now()});navigate('requests');}}>Book a time with your RM<Icon name="arrow"/></button>;
    // A question handed to the relationship manager: its reference is in the reply; follow it in My requests.
    if(message.needs_human&&/My requests/.test(message.content))
      return <button className="bank-chat-logout rm-handoff" data-testid="ticket-handoff" onClick={()=>{setTicketPrefill(null);navigate('requests');}}>See my requests<Icon name="arrow"/></button>;
    return null;
  }
  // Compliance replies end with a hand-off to the review page, plus a toggle for the in-chat review panel under the latest one.
  const lastCompliance=messages.findLastIndex(m=>m.sections?.some(section=>['compliance','document'].includes(section.domain)));
  function complianceStep(message,index) {
    if(index!==lastCompliance||!session||mode!=='personal')return null;
    return <div className="rm-handoff-row"><button className="primary-button rm-handoff" data-testid="compliance-handoff" onClick={()=>navigate('compliance')}>Open compliance review<Icon name="arrow"/></button>
      <button className="bank-chat-logout rm-handoff" data-testid="toggle-compliance-workspace" aria-expanded={showCompliance} onClick={()=>setComplianceOpen(!showCompliance)}>{showCompliance?'Hide review here':'Show review here'}</button></div>;
  }
  const showCompliance=complianceOpen??messages.some(m=>m.sections?.some(section=>['compliance','document'].includes(section.domain)));
  const footer=<footer className="bank-chat-footer"><span>IS4000 · Coursework demo · No real banking services</span></footer>;  // who is signed in is shown by the sidebar profile
  function openActions() {closeOverlays();navigate('actions');}
  const review={label:'Review in Actions',onSelect:openActions},requests={label:'Open My requests',onSelect:()=>navigate('requests')},compliance={label:'Open compliance review',onSelect:()=>navigate('compliance')};
  function closeOverlays() {setSummary(null);setArticle(null);setSavedChats(false);setRecordsOpen(false);}
  async function signOut() {request.current?.abort();await endSession();history.replaceState(null,'',location.pathname+location.search);try{await supabase.auth.signOut();}catch{setNotice('Unable to sign out. Please retry.');}}
  const menu=[
    {testid:'open-records',ariaLabel:'View your customer records',icon:'user',label:'Records',onSelect:()=>{closeOverlays();setRecordsOpen(true);}},
    ...(store==='database'?[{testid:'open-saved-chats',ariaLabel:'Open saved conversations',icon:'history',label:'Saved chats',onSelect:()=>{closeOverlays();navigate('chat');setSavedChats(true);}}]:[]),
    {testid:'open-actions',ariaLabel:'Open actions',icon:'history',label:'Actions',onSelect:openActions},
    {testid:'sign-out',icon:'logout',label:'Sign out',danger:true,onSelect:signOut},
  ];
  // The sidebar and the mobile tab bar list the same pages; for guests every page but the assistant is a sign-in prompt.
  const pages=[
    {view:'overview',testid:'open-overview',icon:'account',label:'Overview',short:'Home'},
    {view:'chat',testid:'open-chat',icon:'chat',label:'Assistant',short:'Assistant'},
    {view:'actions',testid:'nav-actions',icon:'history',label:'Actions',short:'Actions',onSelect:session?openActions:null},
    {view:'compliance',testid:'open-compliance',icon:'document',label:'Compliance review',short:'Review'},
    {view:'requests',testid:'open-requests',icon:'clip',label:'My requests',short:'Requests'},
  ].map(page=>({...page,locked:!session&&page.view!=='chat',onSelect:page.onSelect||(()=>{if(page.view!=='chat')closeOverlays();navigate(page.view);})}));
  const current=view;

  return <div className="bank-chat-page">
    <a className="skip-link" href="#chat-composer">Skip to message</a>
    <TopBar session={session} onSupport={prepareSummary} onSignIn={onSignIn} unread={unread} inboxOpen={view==='inbox'} onInbox={()=>{closeOverlays();navigate('inbox');}}/>
    <div className="bank-chat-layout">
      <Sidebar session={session} name={profileName} menu={menu} current={current} pages={pages} onSignOut={signOut}/>
      <TabBar current={current} pages={pages}/>
      <main className="chat-workspace" hidden={view!=='chat'}>
        <div className="chat-toolbar">
          <label className="pill-select" title="The language applies to AI answers only"><Icon name="info"/><select aria-label="Answer language" disabled={booting} value={language} onChange={e=>{if(store!=='database'||reset())setLanguage(e.target.value);}}>{['English','Chinese','Malay','Tamil'].map(l=><option key={l}>{l}</option>)}</select><Icon name="chevron"/></label>
          <span className="toolbar-spacer"/>
          <button className="bank-chat-logout new-chat" data-testid="new-chat" disabled={booting} onClick={()=>{if(reset())navigate('chat');}}><Icon name="plus"/>New chat</button>
          {store==='database'&&conversation&&<button className="icon-tool" data-testid="reload-saved-chat" aria-label="Refresh this chat" title="Refresh this chat" disabled={loading} onClick={()=>reopen(conversation).catch(failure=>{if(failure.name!=='AbortError')setNotice(failure.message);})}><Icon name="refresh"/></button>}
          {session&&store==='database'&&<button className={`recent-chats ${savedChats?'active':''}`} data-testid="open-recent-chats" aria-expanded={savedChats} onClick={()=>{if(savedChats){closeHistory();return;}closeOverlays();setSavedChats(true);}}><Icon name="history"/>Recent chats{chatCount!==null&&<span className="count">{chatCount}</span>}</button>}
        </div>
        {store==='database'&&conversation&&pending&&!loading&&<div className="pending-bar" role="status"><Icon name="alert"/><span>{workflow?'This reply is waiting for your decision in Actions.':'A reply is still being prepared for this chat.'}</span>
          <span className="pending-actions">{workflow&&<button className="bank-chat-logout" data-testid="review-pending-workflow" onClick={openActions}>Review actions</button>}
          <button className="bank-chat-logout" disabled={loading} onClick={()=>reopen(conversation).catch(failure=>{if(failure.name!=='AbortError')setNotice(failure.message);})}>Refresh</button>
          <button className="bank-chat-logout" data-testid="cancel-pending-reply" onClick={cancelPending}>Cancel reply</button></span></div>}
        <section className="bank-chat-window" aria-label="Banking conversation">
          {booting&&<p className="chat-notice loading" role="status">Opening chat…</p>}
          {bootError&&<p className="chat-notice" role="alert">{bootError}</p>}
          {notice&&(noticeToast?<p key={noticeKey} className="chat-toast" role="status"><Icon name="check"/>{notice}</p>
            :<p key={noticeKey} className="chat-notice-bar" role="status"><Icon name="info"/><span>{notice}</span><button className="icon-button" aria-label="Dismiss message" onClick={()=>setNoticeText('')}><Icon name="close"/></button></p>)}
          <div ref={messageList} className={`bank-chat-messages ${messages.length?'has-messages':''}`} aria-live="polite" aria-relevant="additions text">
            {!messages.length&&<div className="bank-chat-welcome">{session?<><h1 className="welcome-signed-in" aria-busy={profileName===null}>{profileName===null?<><span className="skeleton-text heading" aria-hidden="true"/><span className="sr-only">How can I help today?</span></>:profileName?`Hi ${profileName.split(' ')[0]}, how can I help?`:'How can I help today?'}</h1><p className="chat-greeting" data-testid="chat-greeting">I'm DemoBank's AI assistant. I give general information, not advice.</p><p>Ask about your accounts, cards and loans or your compliance review. If I can't help, I'll pass your question to your relationship manager.</p></>
              :<><span className="eyebrow welcome-eyebrow">A little clarity goes a long way</span><h1>Banking questions.<br/><span>Clearly answered.</span></h1><p className="chat-greeting" data-testid="chat-greeting">I'm DemoBank's AI assistant. I give general information, not advice.</p><p>Understand your accounts, cards and loans. Start with a question.</p></>}<div className="bank-chat-suggestions">{topics.map(t=><button key={t.icon} onClick={()=>draft(t.question)}><Icon name={t.icon}/><strong>{t.title}</strong><span>{t.detail}</span><span className="topic-arrow" aria-hidden="true">↗</span></button>)}</div><div className="quick-help"><span>Need help now?</span><button onClick={()=>draft('I lost my card')}>I lost my card <span aria-hidden="true">↗</span></button><button onClick={()=>draft('How do I find my statement?')}>Find my statement <span aria-hidden="true">↗</span></button></div></div>}
            {messages.map((m,i)=><div key={i} data-testid={`chat-message-${m.role}`} className={`bank-chat-message ${m.role}`} data-response-origin={m.response_origin} data-prompt-version={m.prompt_version}>
              {m.role==='assistant'&&<div className="bank-chat-avatar"><Icon name="spark"/></div>}
              <div className={`bank-chat-bubble ${m.error?'error':''}`}>
                {m.role==='assistant'&&<div className="answer-heading">Banking assistant{m.response_origin&&<small className="answer-origin">{m.response_origin==='ai'?'AI-generated':m.response_origin==='safety'?'Safety guidance':'Answer withheld'}</small>}</div>}
                <Answer message={m} pendingText={loading?'Checking the available information…':''} onSource={showSource} onAction={session?setBlockAction:null}/>
                {ticketStep(m,i)}{complianceStep(m,i)}
                {m.disclaimer&&<details className="answer-disclaimer"><summary><Icon name="info"/>General info, not advice</summary><p>{m.disclaimer}</p></details>}
              </div>
            </div>)}
            {view==='chat'&&session&&mode==='personal'&&showCompliance&&<ComplianceWorkspace key="compliance-workspace" submissionReceipt={complianceReceipt} onSubmitted={setComplianceReceipt} onReviewLoaded={setComplianceReview} onSupport={prepareReviewSupport} onBusyChange={setComplianceBusy} chatBusy={loading||booting||!!bootError||!!pending} onAsk={(question,selection)=>send(null,question,selection)}/>}
          </div>
          <div className="composer-area"><form className="bank-chat-composer" onSubmit={send}>
            <textarea ref={composer} id="chat-composer" aria-label="Your message" rows={1} value={input} onChange={e=>{recall.current=null;setInput(e.target.value);}} disabled={loading||complianceBusy||booting||!!bootError||!!pending} maxLength={4000} placeholder="Ask a banking question…"
              onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();e.currentTarget.form.requestSubmit();}else recallSent(e);}}/>
            <button className="composer-send" data-testid="send-message" aria-label={loading?'Please wait':'Send'} title={loading?'Please wait':'Send'} disabled={loading||complianceBusy||booting||!!bootError||!!pending||!input.trim()}><Icon name="send"/></button>
          </form><div className="composer-note"><span><Icon name="shield"/>{DISCLAIMER}</span>
            <details className="chat-about"><summary>How chats are saved</summary><p>{store==='database'?(session?'Redacted chats are saved to your demo profile; reopen them from Recent chats. Starting a new chat keeps earlier ones.':'This browser keeps access to your redacted guest chat for 24 hours. Starting a new chat clears that access.'):'Chat history is temporary. Refreshing or starting a new chat clears it.'} Older messages may be left out of long chats.</p></details></div></div>
        </section>
        {footer}
      </main>
      {view==='chat'&&savedChats&&<SavedChats closing={historyClosing} current={conversation?.session_id} onLoaded={setChatCount} onClose={closeHistory} onOpen={async item=>{await reopen(item);navigate('chat');}}/>}
      {view!=='chat'&&<main className="app-page">
        {!session&&<LockedPage page={view} onSignIn={onSignIn} onBack={()=>navigate('chat')}/>}
        {session&&view==='overview'&&<OverviewPage key={pageKey} onNavigate={navigate} onAsk={question=>{navigate('chat');draft(question);}} onRequestStatement={()=>requestStatement('')} focus={overviewFocus?.section}/>}
        {session&&view==='inbox'&&<InboxPage key={pageKey} onActions={openActions} links={{Cards:review,Support:requests,Appointments:requests,Requests:requests,'My requests':requests,Relationship:requests,Compliance:compliance,Documents:compliance}}/>}
        {session&&view==='actions'&&<ActionsPanel key={pageKey} onWorkflowChange={status=>{if(status.session_id===conversation?.session_id)setWorkflow(status);}}/>}
        {session&&view==='requests'&&<TicketsPage key={pageKey} prefill={ticketPrefill} onReviewActions={openActions} onSupport={prepareSummary} rmName={rmIntro?.current?.name} initialTab={ticketPrefill?.tab||'open'}/>}
        {session&&view==='compliance'&&<CompliancePage key={pageKey} submissionReceipt={complianceReceipt} onSubmitted={setComplianceReceipt} onReviewLoaded={setComplianceReview} onSupport={prepareReviewSupport} onBusyChange={setComplianceBusy} chatBusy={loading||booting||!!bootError||!!pending} onAsk={(question,selection)=>{setComplianceOpen(true);navigate('chat');return send(null,question,selection);}}/>}
        {footer}
      </main>}
    </div>
    {summary!==null&&session&&<Panel label="Human support" closeLabel="Close summary" title="Talk to a person" subtitle={rmIntro?.current?`Your relationship manager, ${displayName(rmIntro.current.name)}, will pick this up and reply to you.`:'Your relationship manager will pick this up and reply to you.'} onClose={()=>setSummary(null)}
      footer={<><span>IS4000 · Coursework demo · No real banking services</span>{handoff.outcome?<div className="panel-actions"><button className="bank-chat-logout" onClick={()=>setSummary(null)}>Close</button><button className="primary-button" data-testid="handoff-open-requests" onClick={()=>{setSummary(null);navigate('requests');}}>See my requests<Icon name="arrow"/></button></div>:<div className="panel-actions"><button className="bank-chat-logout" data-testid="cancel-handoff" disabled={handoff.saving} onClick={()=>setSummary(null)}>Cancel</button>
        <button className="primary-button" data-testid="approve-handoff" disabled={handoff.saving} onClick={sendHandoff}>Send to my relationship manager<Icon name="arrow"/></button></div>}</>}>
      <label className="form-field">What do you need help with?<select data-testid="handoff-topic" value={helpTopic} disabled={handoff.saving||!!handoff.outcome||!!supportScope} onChange={e=>setHelpTopic(e.target.value)}>{HELP_TOPICS.map(([value,text])=><option key={value} value={value}>{text}</option>)}</select></label>
      {!supportScope&&<label className="sp-attach"><input type="checkbox" checked={attachSummary} disabled={handoff.saving||!!handoff.outcome} onChange={e=>setAttachSummary(e.target.checked)}/><span><strong>Attach a short summary of this chat</strong><small>Topic labels only. Passwords, OTPs and card numbers are never included.</small></span></label>}
      <div className="form-field"><span>What your relationship manager will see</span><p className="handoff-summary" data-testid="handoff-summary">{handoffText}</p></div>
      <p className="security-note"><Icon name="shield"/>Never share your PIN, password or OTP. For a lost card or fraud, ask in the chat first so you can block the card straight away.</p>
      {handoff.saving&&<p className="loading" role="status">Sending…</p>}
      {handoff.error&&<p role="alert">{handoff.error}</p>}
      {handoff.outcome&&<div role="status" data-testid="handoff-receipt"><p>{handoff.outcome.receipt.result.created?'Sent to your relationship manager.':'Your earlier question is still open with your relationship manager.'} You can follow it in My requests.</p></div>}
    </Panel>}
    {summary!==null&&!session&&<Panel label="Human support" closeLabel="Close summary" title="Prepare a support summary" subtitle="Draft a summary you can copy into your bank’s official support channel." onClose={()=>setSummary(null)}><p>This demo has no staffed support queue. For a real issue, use your bank’s official app or website. This draft is not sent anywhere.</p><textarea aria-label="Support summary" rows={7} value={summary} onChange={e=>{setSummary(e.target.value);setApproved(false);setCopied(false);}}/><label className="review-summary"><input type="checkbox" checked={approved} onChange={e=>setApproved(e.target.checked)}/> I reviewed the summary and removed private details</label><div className="panel-actions"><button className="primary-button" disabled={!approved} onClick={async()=>{try{setCopyError('');await navigator.clipboard.writeText(redact(summary));setCopied(true);}catch{setCopyError('Copy unavailable. Select and copy the draft manually.');}}}>Copy summary</button></div>{copied&&<p role="status">Copied. No ticket was sent.</p>}{copyError&&<p role="alert">{copyError}</p>}</Panel>}
    {article&&<Panel label="Source details" closeLabel="Close source" title={article.title} onClose={()=>setArticle(null)}><p className="source-notice">{article.notice}</p><p>{article.content}</p><p className="source-publisher">Publisher: {article.publisher}</p>
      {article.reference_url&&<p><a href={article.reference_url} target="_blank" rel="noopener noreferrer">Read the original source <span aria-hidden="true">↗</span></a></p>}
      {article.checked_on&&<p>Source checked: {article.checked_on}. Review due: {article.review_due}.</p>}
      {article.current===false&&<p role="status">This source needs review and is not used for new answers.</p>}
      <small>Version {article.version}</small></Panel>}
    {blockAction&&(()=>{
      const Dialog=blockAction.type==='card.dispute'?DisputeDialog:CardBlockDialog;
      return <Dialog action={blockAction} onClose={()=>setBlockAction(null)} onDecided={()=>{
        if(conversation)reopen(conversation).catch(failure=>{if(failure.name!=='AbortError')setNotice(failure.message);});
      }}/>;
    })()}
    {recordsOpen&&<RecordsPanel onClose={()=>setRecordsOpen(false)} onReviewActions={openActions}/>}
  </div>;
}