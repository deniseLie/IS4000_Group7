import { useEffect, useRef, useState } from 'react';
import Icon from './components/Icon';
import Panel from './components/Panel';
import Answer from './components/Answer';
import TicketsPage from './components/TicketsPage';
import InlineRequestCard from './components/InlineRequestCard';
import {limitRequestPrefill,ticketIntent,isLimitWorkflowReply,isConceptQuestion,statementRequestPrefill,sendHandoffMessage} from './lib/ticketData';
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
import MeetingsPage from './components/MeetingsPage';
import ClientWelcome from './components/ClientWelcome';
import OfferBlock from './components/OfferBlock';
import MeetingRequestCard from './components/MeetingRequestCard';
import {parseMeetingDraft} from './lib/meetingRequests';
import SharingPage from './components/SharingPage';
import {chatTopicsNote,loadTalkingPoints} from './lib/talkingPoints';
import ComplianceWorkspace from './components/ComplianceWorkspace';
import {loadIntroduction,relationshipIntent} from './lib/relationshipWorkflow';
import {CARE_CHIP,CONFIRM_REPORT,followUps} from './lib/followUps';
import {pageLink} from './lib/pageLinks';
import {canStartFraudReport} from './lib/fraudEntry';
import {prepareComplianceSupport} from './lib/compliance';
import TopBar from './components/TopBar';
import Sidebar,{TabBar} from './components/Sidebar';
import LockedPage from './components/LockedPage';
import { supabase } from './lib/supabase';
import { boundedHistory, handoffMessage, handoffSummary, readEvents, recallStep, redact, sentHistory, supportSummary } from './lib/chatTools';
import {capabilities,clearActive,listConversations,createConversation,loadConversation,readActive,writeActive,newCustomerAccess,newGuestAccess,parseMetadata,cancelConversationTurn} from './lib/conversations';
import {OFFER_REPLIES,fill,offerReply,offerState,parseOffer,placeholders} from './lib/offers';
import {createActionCase,loadActions,proposeAction,rejectAction} from './lib/actions';

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
const PAGES=['chat','overview','meetings','inbox','compliance','requests','actions','sharing'];  // one client view (the RM and admin pages are not shown)
// CR8 routing for "Talk to a person". Michelle is a private-banking client, so her relationship manager is her person for
// general, account and compliance questions (the RM coordinates with the compliance team). Fraud and lost cards go
// to the chat's urgent card request to the RM (build plan 2b); card limit changes go to the cards and credit team's request form.
// [title, explanation, secondary [label, action] or null, primary [label, action] where 'send' = send to the RM]
const HELP_ROUTES={
  general:['Your relationship manager can help','Tip: the assistant answers most general questions instantly from approved bank information.',['Ask the assistant','chat'],['Send to my relationship manager','send']],
  fraud:['Tell your relationship manager now','The assistant gives you the urgent steps and sends this to your relationship manager as urgent. You’ll see the details to confirm before anything is sent. If your card is at risk, freeze it on Home or call the number on the back of your card.',['Lost or stolen card','lost'],['Charge I don’t recognise','charge']],
  card:['Card requests go to the cards and credit team','Limit changes and other card requests are decided by the cards and credit team. Raise the request and you’ll get a reference to follow in Requests.',null,['Open a card request','card']],
  compliance:['Your relationship manager passes this to the review team','Your documents and the bank’s questions are on the Periodic review page.',['Open Periodic review','compliance'],['Send to my relationship manager','send']],
};
const HELP_TOPICS=[['general','A banking question'],['records','My accounts, spending or deposits'],['fraud','A charge I don’t recognise or a lost card'],['card','A card request (limit or other change)'],['compliance','My periodic review'],['relationship','Advice, fees or anything else']];
// The first line of the drafted message, in her words, for each topic.
const HELP_OPENING={general:'I have a banking question',records:'I have a question about my accounts',fraud:'I need help with my card',card:'I’d like to change something on my card',compliance:'I have a question about my periodic review',relationship:'I’d like your help with something'};
// Talk to a person starts on the topic of her last question (she can still pick another).
function guessHelpTopic(question) {
  const q=String(question||'');
  if(/\b(?:unrecogni[sz]ed|don.?t recogni[sz]e|fraud|stolen|lost (?:my )?card|suspicious)\b/i.test(q))return 'fraud';
  if(/\b(?:limit|replace(?:ment)? card|new card)\b/i.test(q))return 'card';
  if(/\b(?:review|compliance|kyc|proof of address|document)\b/i.test(q))return 'compliance';
  if(/\b(?:spen[dt]|balance|account|deposit|statement|transaction|transfer|payment|matur\w*|savings?)\b/i.test(q))return 'records';
  if(/\b(?:advice|invest\w*|loan|mortgage|fee|meeting)\b/i.test(q))return 'relationship';
  return 'general';
}
// Guests can open every page's address too; they see a sign-in prompt instead of records.
function pageFromHash(session) {const page=location.hash.slice(1);return PAGES.includes(page)||!session&&page==='actions'?page:'chat';}
// The header badge says the data is fictional and each answer carries its own "General info, not advice" note.
const DISCLAIMER = 'AI answers can contain errors. Never share your PIN, password or OTP.';

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
  const [now,setNow]=useState(()=>Date.now());  // re-renders offer expiry
  const [savedChats,setSavedChats]=useState(false),[historyClosing,setHistoryClosing]=useState(false);
  const historyTimer=useRef(null);
  // The history column narrows to nothing before it unmounts, so the chat widens at the same pace.
  function closeHistory() {
    if(!savedChats||historyTimer.current)return;
    if(window.matchMedia('(prefers-reduced-motion: reduce)').matches){setSavedChats(false);return;}
    setHistoryClosing(true);
    historyTimer.current=setTimeout(()=>{historyTimer.current=null;setHistoryClosing(false);setSavedChats(false);},260);
  }
  const [recordsOpen,setRecordsOpen]=useState(false),[toConfirm,setToConfirm]=useState(0);  // Activity badge: actions waiting for her OK
  const [complianceOpen,setComplianceOpen]=useState(null);
  const [complianceBusy,setComplianceBusy]=useState(false);
  const [complianceReceipt,setComplianceReceipt]=useState(null);
  const [complianceReview,setComplianceReview]=useState(null),[supportScope,setSupportScope]=useState(null);
  const [rmIntro,setRmIntro]=useState(null),[profileName,setProfileName]=useState(session?null:'');  // null while the name is loading
  const [handoff,setHandoff]=useState({saving:false,outcome:null,error:''});
  const [rmMessage,setRmMessage]=useState(''),rmEdited=useRef(false);
  const [helpTopic,setHelpTopic]=useState('relationship'),[attachSummary,setAttachSummary]=useState(true);
  const [unread,setUnread]=useState(0),[chatCount,setChatCount]=useState(null);
  const [chatTopics,setChatTopics]=useState(null);  // {on, rm}: the line under the chat while chat topics are shared (0108)
  const [view,setView]=useState(()=>pageFromHash(session));
  const [pageKey,setPageKey]=useState(0);
  const [ticketPrefill,setTicketPrefill]=useState(null);  // manual My Requests workflow
  // A draft is anchored to the assistant reply that created it, never a drawer.
  const [inlineRequest,setInlineRequest]=useState(null);
  const [freshDraftReply,setFreshDraftReply]=useState(null); // auto card only on a newly generated reply, not a saved transcript

  const [overviewFocus,setOverviewFocus]=useState(null);  // section to scroll to when Overview opens from the assistant
  const [sharingFocus,setSharingFocus]=useState(null);  // the Data sharing detail to open (Manage under the composer opens chat topics)
  const messageList=useRef(null);
  const composer=useRef(null);
  const recall=useRef(null);
  const request=useRef(null);
  const pendingInlineProposal=useRef(null);
  const version=useRef(0);
  useEffect(()=>{
    const list=messageList.current;
    if(!messages.length||!list)return;
    const behavior=window.matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth';
    const answer=[...list.querySelectorAll('[data-testid="chat-message-assistant"]')].at(-1);
    const scroll=()=>list.scrollTo({top:!loading&&messages.at(-1).role==='assistant'&&answer?answer.getBoundingClientRect().top-list.getBoundingClientRect().top+list.scrollTop:list.scrollHeight,behavior});
    scroll();
    // Keep the answer's beginning visible when its sources and follow-up controls finish rendering.
    const later=setTimeout(scroll,350);
    return ()=>clearTimeout(later);
  },[messages,loading]);
  useEffect(()=>()=>request.current?.abort(),[]);
  useEffect(()=>{  // the message box grows with its text, up to about six lines, then scrolls
    const box=composer.current;if(!box)return;
    box.style.height='auto';box.style.height=`${Math.min(box.scrollHeight,160)}px`;
  },[input]);

  useEffect(()=>{  // an open offer's buttons turn off when it expires; the countdown refreshes each half minute
    const open=messages.findLast(m=>m.offer);
    if(!open||Date.parse(open.offer.expires_at)<=Date.now())return;
    const tick=setInterval(()=>setNow(Date.now()),30000);
    const expiry=setTimeout(()=>setNow(Date.now()),Date.parse(open.offer.expires_at)-Date.now()+50);
    return ()=>{clearInterval(tick);clearTimeout(expiry);};
  },[messages]);
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
          let fresh=false;
          try {fresh=!!session&&sessionStorage.getItem('demobank.fresh-sign-in')==='1';sessionStorage.removeItem('demobank.fresh-sign-in');} catch {}
          if(fresh)clearActive(session.user.id);  // just signed in: start on the welcome page, not the last saved chat
          const access=fresh?null:readActive(session?.user.id||null);
          if(access) {
            const history=await loadConversation(access,await sessionHeaders(),controller.signal);
            if(!controller.signal.aborted)restore(history,access);
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

  useEffect(()=>{  // whether topics from her saved chats may reach her RM (her chat_topics grant); read again after Data sharing
    if(!session||store!=='database'||view==='sharing'){if(!session)setChatTopics(null);return;}
    const controller=new AbortController();
    (async()=>{try{const headers=await sessionHeaders();const topics=await loadTalkingPoints(headers.Authorization.replace(/^Bearer /,''),controller.signal);
      if(!controller.signal.aborted)setChatTopics({on:topics.chat_topics,rm:topics.rm_first_name});}catch{if(!controller.signal.aborted)setChatTopics(null);}})();
    return ()=>controller.abort();
  },[session?.user.id,store,view==='sharing']);

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

  function restore(history,access) {
    writeActive(session?.user.id||null,access);
    setConversation({...history.session,guest_token:access.guest_token});setPending(history.active_turn_id);
    setMessages(history.messages);setMode(history.session.mode);setLanguage(history.session.language);setInput('');setBootError('');setNow(Date.now());
    setSummary(null);setArticle(null);setInlineRequest(null);setFreshDraftReply(null);closeHistory();setRecordsOpen(false);
    setNotice(history.history_trimmed?'Opened the latest saved messages. Older messages are omitted from this long chat.':'Saved chat reopened.',!history.history_trimmed);
  }

  async function reopen(item) {
    version.current++;request.current?.abort();
    const epoch=version.current,controller=new AbortController();request.current=controller;setLoading(true);
    const access={session_id:item.session_id,guest_token:conversation?.session_id===item.session_id?conversation.guest_token:null};
    try {
      const history=await loadConversation(access,await sessionHeaders(),controller.signal);
      if(epoch===version.current)restore(history,access);
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
    if(pendingInlineProposal.current){setNotice('A request is awaiting your decision. Cancel that draft in the chat or Actions before starting a new chat.');return false;}
    if(store==='database') {
      try {clearActive(session?.user.id||null);}
      catch(failure) {setBootError(failure.message);return false;}
    }
    version.current++;request.current?.abort();request.current=null;
    setMessages([]);setInput('');setLoading(false);setMode(session?'personal':'public');setNotice('New chat started.',true);
    setSummary(null);setInlineRequest(null);setFreshDraftReply(null);setApproved(false);setCopied(false);setCopyError('');setArticle(null);
    setConversation(null);setPending(null);setSavedChats(false);setRecordsOpen(false);setComplianceOpen(null);setSupportScope(null);if(store!==null)setBootError('');
    return true;
  }

  async function endSession() {  // records auth.session_ended; sign-out goes ahead even if this fails
    try {
      const {data}=await supabase.auth.getSession();
      if(data.session)await fetch(`${API_URL}/auth/session-end`,{method:'POST',headers:{Authorization:`Bearer ${data.session.access_token}`}});
    } catch {}
  }

  async function send(e,supplied=null,recordContext=null,offer=null) {
    e?.preventDefault();
    const question=(supplied??input).trim();if(!question||loading||booting||bootError||pending||(e&&complianceBusy))return;
    const safe=redact(question);
    if(safe!==question) {
      setInput('');setNotice('Private details were removed. Never share passwords, PINs, OTPs, CVVs or full card numbers in chat. Please ask again without them. If your real card is lost or stolen, contact your bank immediately through its official app or website.');return;
    }
    // A newly typed correction invalidates the old pending proposal before it
    // can be submitted. Never leave a stale ticket draft behind in Actions.
    if(pendingInlineProposal.current) {
      try {
        const commandId=pendingInlineProposal.current;
        const access=await sessionHeaders();
        await rejectAction(commandId,access.Authorization.replace(/^Bearer /,''));
        pendingInlineProposal.current=null;
      }catch(error) {
        setNotice(`Please cancel the pending request review before continuing. ${error.message}`);
        return;
      }
    }
    const visible=[...messages,{role:'user',content:safe}];
    const history=boundedHistory(visible);
    if(history.length<visible.filter(m=>!m.error).length)setNotice('Older messages are no longer sent. Please restate any older details that still matter.');
    setInlineRequest(null);setFreshDraftReply(null);setMessages([...visible,{role:'assistant',content:''}]);setInput('');setLoading(true);
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
      const response=await fetch(`${API_URL}/chat`,{method:'POST',headers,body:JSON.stringify({messages:history,mode,language,...stored,...(recordContext&&{record_context:recordContext}),...(offer&&{offer})}),signal:controller.signal});
      if(!response.ok) {
        const code=await response.json().then(b=>b?.error?.code,()=>null);
        throw new Error(ERRORS[code]||'The banking service is unavailable. Please retry.');
      }
      if(!response.body)throw new Error('No response received.');
      let live=[];  // this reply's sections as they stream: a placeholder per planned task, filled in place (never reordered)
      // Parts are not shown one by one as they arrive: the three dots stay until the whole answer is ready, then all parts appear together.
      const streaming=()=>{};
      await readEvents(response.body,(type,data)=>{
        if(epoch!==version.current)return;
        if(type==='conversation') {
          const metadata=parseMetadata(JSON.parse(data));
          if(metadata.conversation_id!==thread?.session_id||metadata.revision<thread.revision)throw new Error('The saved reply does not match this chat. Reload it before retrying.');
          setConversation({...thread,revision:metadata.revision,active_turn_id:metadata.active_turn_id});setPending(metadata.active_turn_id);
        }
        if(type==='plan'){live=placeholders(JSON.parse(data));streaming();}
        if(type==='section'){live=fill(live,JSON.parse(data));streaming();}
        if(type==='answer') {
          const result=JSON.parse(data);
          // Several questions: parts in the order she asked them (an urgent card part still first), not the order agents finished.
          const order=section=>[section.priority==='P0'?0:1,Number(String(section.task_id||'').replace(/\D/g,''))||0];
          const sections=Array.isArray(result.sections)?[...result.sections].sort((x,y)=>{const a=order(x),b=order(y);return a[0]-b[0]||a[1]-b[1];}):result.sections;
          setFreshDraftReply(visible.length);setNow(Date.now());setMessages([...visible,{role:'assistant',content:result.answer,...result,...(sections?{sections}:{}),offer:parseOffer(result.offer)}]);
        }
      });
    } catch(err) {
      if(err.name!=='AbortError'&&epoch===version.current)setMessages([...visible,{role:'assistant',content:err.message,error:true}]);
    } finally {
      if(epoch===version.current){setLoading(false);request.current=null;}
    }
  }

  async function loadSource(source) {
    const r=await fetch(`${API_URL}${source.url}`);if(!r.ok)throw new Error('Source unavailable');
    return r.json();
  }
  function pageLinkFor(message,index) {
    // The periodic review answer: open the page, or show the review here (in the chat), as two links inside the answer.
    if(index===lastCompliance&&session&&mode==='personal'&&!message.error&&message.response_origin!=='validation')
      return {label:'Open Periodic review',testid:'compliance-handoff',go:()=>navigate('compliance'),
        second:{label:showCompliance?'Hide review here':'Show review here',testid:'toggle-compliance-workspace',expanded:showCompliance,go:()=>setComplianceOpen(!showCompliance)}};
    const found=message.role==='assistant'?ticketStep(message,index,true):null;
    return found&&typeof found==='object'&&'go' in found?found:null;
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
    setOverviewFocus(null);setSharingFocus(null);
    if(page===view){if(page!=='chat')setPageKey(key=>key+1);return;}  // choosing the open page reloads it
    if(page==='chat')history.pushState(null,'',location.pathname+location.search);else location.hash=page;
    setView(page);
  }
  function draft(question) { setInput(question);requestAnimationFrame(()=>composer.current?.focus()); }
  // The offer buttons answer the whole group; the typed reply shown is what the button means.
  function decideOffer(message,decision,on={}) {send(null,OFFER_REPLIES[decision],null,offerReply(message.offer,decision,on));}  // on: her switches (0114)
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
  function prepareSummary() {
    rmEdited.current=false;
    const last=[...messages].reverse().find(item=>item.role==='user'&&item.content&&!/^talk to a person$/i.test(item.content.trim()))?.content;
    const guessed=guessHelpTopic(last);
    // The review-specific summary only on the review page, or in the chat when her last question was about the review.
    if(session&&(view==='compliance'||view==='chat'&&showCompliance&&guessed==='compliance')&&complianceReview){prepareReviewSupport({review_case_id:complianceReview});return;}
    setSupportScope(null);setAttachSummary(true);setSummary(session?handoffSummary(messages):supportSummary(messages));setApproved(false);setCopied(false);setCopyError('');setHandoff({saving:false,outcome:null,error:''});setHelpTopic(guessed);
  }
  async function sendHandoff() {  // signed in: records a real support handoff (an automatic action) from the label-only summary
    if(!rmMessage.trim()){setHandoff({saving:false,outcome:null,error:'Write a short message for your relationship manager first.'});return;}
    setHandoff({saving:true,outcome:null,error:''});
    try {
      const reviewedMessage=handoffMessage(rmMessage,supportScope||attachSummary?handoffText:'');
      const clientMessage=redact(rmMessage.trim());
      if(supportScope&&clientMessage.length>480)throw new Error('Shorten your compliance message to 480 characters; the recorded review context is attached separately.');
      const {data,error}=await supabase.auth.getSession();
      if(error)throw error;
      if(!data.session)throw new Error('Session expired. Please sign in again.');
      const token=data.session.access_token;
      const scope=supportScope?{case_id:supportScope}:await createActionCase({session_id:crypto.randomUUID(),case_id:crypto.randomUUID()},token);
      const urgent=messages.some(m=>m.sections?.some(section=>section.priority==='P0'));
      const saved=await proposeAction({command_id:crypto.randomUUID(),task_id:'handoff',case_id:scope.case_id,type:'handoff.create',
        target:{reason:'general_support',priority:urgent?'P0':'P2',summary:supportScope?handoffText:reviewedMessage}},token);
      if(saved.receipt?.outcome!=='succeeded')throw new Error('Your question could not be sent. Please try again.');
      if(supportScope)await sendHandoffMessage(saved.receipt.result.handoff_id,clientMessage,token);
      setHandoff({saving:false,outcome:saved,error:''});
      setNotice('Sent to your relationship manager. You can follow it in Requests.');
    } catch(failure) {setHandoff({saving:false,outcome:null,error:failure.message});}
  }
  const topicLabel=HELP_TOPICS.find(entry=>entry[0]===helpTopic)[1];
  const route=HELP_ROUTES[helpTopic]||null;
  // Opened from Compliance review she starts on that topic, but can still pick any other; the review-specific summary
  // is then replaced by the chat summary used everywhere else.
  function changeTopic(value) {
    setHelpTopic(value);
    if(supportScope&&value!=='compliance'){setSupportScope(null);setAttachSummary(true);setSummary(handoffSummary(messages));}
  }
  function followRoute(action) {
    if(action==='send')return sendHandoff();
    setSummary(null);
    if(action==='card'){setTicketPrefill({category:'card_limit_change',key:Date.now()});navigate('requests');}
    else if(action==='overview')navigate('overview');
    else if(action==='compliance')navigate('compliance');
    else {
      navigate('chat');
      // Fraud gets the fixed urgent card steps and one urgent request to her RM, recorded only when she confirms (2b).
      if(action==='charge')send(null,'I don’t recognise a charge on my card');
      else if(action==='lost')send(null,'I lost my card');
    }
  }
  // Plain words for the RM: what she asked for first, then the earlier topics of this chat (labels only, never her text).
  // Topic labels without the internal "×2" counts (how many times a topic came up), which mean nothing to her or the RM.
  const earlierTopics=summary&&attachSummary?((/Topics: ([^.]+)\./.exec(summary)||[])[1]||'').replace(/\s*×\d+/g,'')||null:null;
  const handoffText=summary===null?null:supportScope?summary:`Asked to talk to a person about: ${topicLabel}.${earlierTopics?` Earlier in this chat: ${earlierTopics}.`:''}`;
  // CR8: the assistant drafts the message to the RM in plain words; she reads it, edits or adds to it, then sends it.
  const lastQuestion=[...messages].reverse().find(item=>item.role==='user'&&item.content&&!/^talk to a person$/i.test(item.content.trim()))?.content;
  const rmFirst=rmIntro?.current?displayName(rmIntro.current.name).split(' ')[0]:null;
  // Earlier topics only when she asked more than one thing (one question is already quoted in full).
  const askedCount=messages.filter(item=>item.role==='user'&&!/^talk to a person$/i.test(String(item.content||'').trim())).length;
  const rmDraft=summary===null?'':[`Hi${rmFirst?` ${rmFirst}`:''}, ${supportScope?HELP_OPENING.compliance:HELP_OPENING[helpTopic]}.`,
    lastQuestion&&!supportScope?`My question: “${redact(lastQuestion).slice(0,300)}”`:'',
    earlierTopics&&askedCount>1?`Earlier I also asked about: ${earlierTopics.toLowerCase()}.`:'',
    'Thank you.'].filter(Boolean).join('\n');
  useEffect(()=>{if(!rmEdited.current)setRmMessage(rmDraft);},[rmDraft]);  // fixed labels only

  function openInlineRequest(prefill={},index=null) {
    const anchor=index??messages.findLastIndex(item=>item.role==='assistant');
    setInlineRequest({...prefill,index:anchor,key:Date.now()});
    navigate('chat');
    requestAnimationFrame(()=>messageList.current?.scrollTo({top:messageList.current.scrollHeight,behavior:'smooth'}));
  }
  function requestStatement(summary,index=null) {openInlineRequest({category:'statement_request',summary,details:statementRequestPrefill(summary)},index);}
  function readyForInlineCard(prefill) {
    const d=prefill?.details||{};
    return !!(d.new_limit&&d.duration&&(d.duration==='permanent'||d.until)&&prefill.selectedCardLabel);
  }
  function inlineCardFor(message,index) {
    if(!session||mode!=='personal'||message.role!=='assistant'||!message.content||message.error||message.response_origin==='validation'||message.source_check==='rejected')return null;
    const chosen=inlineRequest?.index===index?inlineRequest:null;
    if(chosen?.dismissed)return null;
    const latest=index===messages.length-1&&!loading&&!pending;
    const asked=messages[index-1]?.role==='user'?messages[index-1].content:'';
    const intent=ticketIntent(asked);
    const validLimit=!isConceptQuestion(asked)&&intent!=='status'&&(intent==='card_limit_change'||isLimitWorkflowReply(message));
    const guessed=validLimit?{category:'card_limit_change',...limitRequestPrefill(messages.slice(0,index).filter(item=>['user','assistant'].includes(item.role)))}:null;
    const automatic=latest&&freshDraftReply===index&&guessed&&readyForInlineCard(guessed)?guessed:null;
    const selected=chosen||automatic;
    if(!selected)return null;
    return <InlineRequestCard key={chosen?.key||`automatic-${conversation?.session_id||'new'}-${index}`}
      prefill={selected} onDismiss={()=>setInlineRequest({index,key:Date.now(),dismissed:true})}
      onProposalChange={id=>{pendingInlineProposal.current=id;}} onSuccess={()=>setPageKey(k=>k+1)}/>;
  }
  // Request categories (CL1/TK1/TK2): a matching question, or a reply handed to the RM, ends with a button to My requests.
  function ticketStep(message,index,inline=false) {
    // Going to a page is a link inside the answer (inline); actions (start a request, report a charge) stay buttons below it.
    const nav=(label,testid,go)=>inline?{label,testid,go}:null;
    if(message.role!=='assistant'||!message.content||!session||mode!=='personal')return null;
    const asked=messages[index-1]?.role==='user'?messages[index-1].content:'';
    let intent=ticketIntent(asked);
    if(!intent&&/\b(?:actually|instead|rather)\b[^.?!]{0,40}\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\b/i.test(asked)) {
      const prior=messages.slice(0,index-1).filter(item=>item.role==='user').slice(-2);
      if(prior.some(item=>['statements','statement_request'].includes(ticketIntent(item.content))))intent='statements';
    }
    // Definitions and comparisons are information requests, never automatic service intake.
    const concept=isConceptQuestion(asked);
    // A bank decision/status enquiry can only read an existing ticket; it must
    // never offer to start another card limit application.
    if(intent==='status' && message.response_origin==='validation')return null;
    // CR12: entering fraud support is not filing a report. Since 2b a signed-in fraud reply carries the urgent RM
    // request as an offer with its own Confirm; this button shows only on the latest fraud reply without one
    // (lib/fraudEntry.js).
    if(canStartFraudReport({message,signedIn:!!session&&mode==='personal',latest:index===messages.length-1}))return nav('Report suspicious transaction','fraud-report-entry',()=>send(null,'I suspect an unauthorised card transaction. Please help me identify the affected card and charge so I can review and confirm an urgent fraud report.'));
    if(intent==='status')return nav('Open Requests','ticket-handoff',()=>{setTicketPrefill(null);navigate('requests');});
    if(!concept&&(intent==='card_limit_change'||isLimitWorkflowReply(message))&&!message.error&&message.source_check!=='rejected'&&!/Answer withheld|couldn’t confirm|couldn't verify/i.test(message.content)) {
      const prefill={category:'card_limit_change',...limitRequestPrefill(messages.slice(0,index).filter(item=>['user','assistant'].includes(item.role)))};
      // When enough has been gathered, the inline card renders automatically after
      // the reply. Incomplete requests can be opened here to fill missing fields.
      if(readyForInlineCard(prefill)||inlineRequest?.index===index)return null;
      return nav('Review request details','ticket-handoff',()=>openInlineRequest(prefill,index));
    }
    // The client can ask to start another service request in the chat itself.
    if(!concept&&/\b(?:raise|open|create|submit|start)\s+(?:a\s+|an\s+)?(?:new\s+)?(?:service\s+)?request\b/i.test(asked)&&intent!=='status'&&message.response_origin!=='validation'&&!message.error)
      return nav('Prepare a request in chat','request-entry',()=>openInlineRequest({},index));
    // CR15: a statement question ends with a way to the Statements section, or to a statement copy request for older ones.
    if(intent==='statements')return nav('Open Statements','statement-handoff',()=>{navigate('overview');setOverviewFocus({section:'statements',key:Date.now()});});
    if(intent==='personal_details_update'&&message.response_origin!=='validation')return nav('Prepare details update','personal-details-entry',()=>openInlineRequest({category:'personal_details_update',summary:redact(asked).slice(0,500)},index));
    if(intent==='statement_request')return nav('Request a statement copy','ticket-handoff',()=>requestStatement(redact(asked).slice(0,500),index));
    // 2a: a meeting question ends with a way to the Meetings page, where she follows her meeting requests (or makes one there).
    if(relationshipIntent(asked)==='meeting')return nav('Open Meetings','meeting-handoff',()=>navigate('meetings'));
    // A question handed to the relationship manager: its reference is in the reply; follow it in My requests.
    if(message.needs_human&&/My requests/.test(message.content))
      return nav('Open Requests','ticket-handoff',()=>{setTicketPrefill(null);navigate('requests');});
    // CR2: otherwise a way to the page the question was about (not on previews, choices or withheld answers).
    if(index===lastCompliance)return null; // One compliance navigation action; the optional inline review is separate.
    const link=!message.error&&message.source_check!=='rejected'&&message.response_origin!=='validation'&&!/Here’s what I’d send|Answer withheld|couldn’t confirm|couldn.t verify/i.test(message.content)?pageLink(asked):null;
    if(!link)return null;
    const go={meetings:()=>navigate('meetings'),requests:()=>{setTicketPrefill(null);navigate('requests');},
      compliance:()=>navigate('compliance'),inbox:()=>{closeOverlays();navigate('inbox');}}[link.key]
      ||(()=>{navigate('overview');setOverviewFocus({section:link.key,key:Date.now()});});
    return nav(link.label.replace(/^Go to /,'Open '),'page-link',go);
  }
  // Compliance replies end with a hand-off to the review page, plus a toggle for the in-chat review panel under the latest one.
  const lastCompliance=messages.findLastIndex(m=>m.sections?.some(section=>['compliance','document'].includes(section.domain)));
  // Suggested next questions under the latest answer, for clients not sure what to ask next (fixed lists, lib/followUps.js).
  // A meeting request the assistant drafted (one meeting.request offer in its fixed wording) is shown as an editable card.
  function meetingDraft(message) {
    if(message.role!=='assistant'||!session||message.offer?.offers?.length!==1||message.offer.offers[0].type!=='meeting.request')return null;
    // Only the part that drafted the request goes in the card; other questions in the same message are answered above it.
    const own=(message.sections||[]).find(section=>/this meeting request:/.test(section.answer||''));
    return parseMeetingDraft(own?own.answer:message.content);
  }
  function otherParts(message) {
    const rest=(message.sections||[]).filter(section=>!/this meeting request:/.test(section.answer||''));
    return rest.length?{...message,sections:rest}:null;
  }
  function nextList(message,index) {
    const asked=messages.filter(m=>m.role==='user').map(m=>m.content);
    const words=text=>new Set(String(text).toLowerCase().replace(/[^a-z0-9 ]/g,' ').split(/\s+/).filter(w=>w.length>2));
    const same=(a,b)=>{const x=words(a),y=words(b);const both=[...x].filter(w=>y.has(w)).length;return both/Math.max(1,Math.min(x.size,y.size))>=0.75;};
    return followUps({question:messages[index-1]?.role==='user'?messages[index-1].content:'',message,signedIn:!!session&&mode==='personal',asked})
      .filter(text=>/\bTCK-\d+/.test(text)||!asked.some(question=>same(question,text)));  // a reference to pick is never "already asked"
  }
  function nextQuestions(message,index) {
    if(index!==messages.length-1||loading||pending)return null;
    const list=nextList(message,index);
    if(!list.length)return null;
    const label=list[0]===CONFIRM_REPORT?'Are these the charges?':/^What is the status of TCK-/.test(list[0])&&list.length>1?'Which request?':list.includes('Both cards')?'Which card?':list[0]===CARE_CHIP?'We’re here to help':/^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d/.test(list[0])?'Times that suit you':list.includes('Cancel')?'Send it?':'Next:';
    return <div className="follow-ups" data-testid="follow-ups"><span>{label}</span>
      {(label==='Next:'?list.slice(0,3):list).map(text=><button key={text} type="button" data-testid="follow-up" disabled={booting||!!bootError||complianceBusy} onClick={()=>send(null,text)}>{text.replace(/^What is the status of (TCK-\d+)\?$/,'$1')}</button>)}</div>;
  }
  const showCompliance=complianceOpen===true;
  const footer=null;  // who is signed in is shown by the sidebar profile
  function openActions() {closeOverlays();navigate('actions');}
  useEffect(()=>{if(!session)return;const c=new AbortController();(async()=>{try{const {data}=await supabase.auth.getSession();if(!data.session)return;
    const items=await loadActions(data.session.access_token,c.signal);if(!c.signal.aborted)setToConfirm(items.filter(item=>item.command.state==='proposed').length);}catch{}})();return ()=>c.abort();},[session?.user.id,view,pageKey]);  // eslint-disable-line react-hooks/exhaustive-deps
  const review={label:'Review in Actions',onSelect:openActions},requests={label:'Open Requests',onSelect:()=>navigate('requests')},compliance={label:'Open Periodic review',onSelect:()=>navigate('compliance')};
  function closeOverlays() {setSummary(null);setArticle(null);setSavedChats(false);setRecordsOpen(false);}
  async function signOut() {request.current?.abort();await endSession();history.replaceState(null,'',location.pathname+location.search);try{await supabase.auth.signOut();}catch{setNotice('Unable to sign out. Please retry.');}}
  const menu=[
    // Records is on Home (Your details › See all your records), Saved chats is "Recent chats" in the assistant and
    // Activity is in the menu, so only Data sharing stays here.
    {testid:'open-data-sharing',ariaLabel:'Open data sharing',icon:'shield',label:'Data sharing',onSelect:()=>{closeOverlays();navigate('sharing');}},
    {testid:'sign-out',icon:'logout',label:'Sign out',danger:true,onSelect:signOut},
  ];
  // The sidebar and the mobile tab bar list the same pages; for guests every page but the assistant is a sign-in prompt.
  const pages=[
    // Client menu (redesign): Home, Assistant, Meetings (its own page), Requests, Periodic review, Activity.
    {view:'overview',testid:'open-overview',icon:'account',label:'Home',short:'Home'},
    {view:'chat',testid:'open-chat',icon:'chat',label:'Assistant',short:'Assistant'},
    {view:'meetings',testid:'open-meetings',icon:'calendar',label:'Meetings',short:'Meetings'},
    {view:'requests',testid:'open-requests',icon:'clip',label:'Requests',short:'Requests'},
    {view:'compliance',testid:'open-compliance',icon:'document',label:'Periodic review',short:'Review'},
    {view:'actions',testid:'nav-actions',icon:'history',label:'Activity',short:'Activity',onSelect:session?openActions:null,badge:toConfirm,badgeLabel:'to confirm'},
  ].map(page=>({...page,locked:!session&&page.view!=='chat',onSelect:page.onSelect||(()=>{if(page.view!=='chat')closeOverlays();navigate(page.view);})}));
  const current=view;

  return <div className="bank-chat-page">
    <a className="skip-link" href="#chat-composer">Skip to message</a>
    <TopBar session={session} onSupport={prepareSummary} onSignIn={onSignIn} unread={unread} inboxOpen={view==='inbox'} onInbox={()=>{closeOverlays();navigate('inbox');}}/>
    <div className="bank-chat-layout">
      <Sidebar session={session} name={profileName} menu={menu} current={current} pages={pages} onSignOut={signOut} onSignIn={onSignIn}/>
      <TabBar current={current} pages={pages}/>
      <main className="chat-workspace" hidden={view!=='chat'}>
        <div className="chat-toolbar">
          <label className="pill-select" title="The language applies to AI answers only"><Icon name="info"/><select aria-label="Answer language" disabled={booting} value={language} onChange={e=>{if(store!=='database'||reset())setLanguage(e.target.value);}}>{['English','Chinese','Malay','Tamil'].map(l=><option key={l}>{l}</option>)}</select><Icon name="chevron"/></label>
          <span className="toolbar-spacer"/>
          <button className="bank-chat-logout new-chat" data-testid="new-chat" disabled={booting} onClick={()=>{if(reset())navigate('chat');}}><Icon name="plus"/>New chat</button>
          {store==='database'&&conversation&&<button className="icon-tool" data-testid="reload-saved-chat" aria-label="Refresh this chat" title="Refresh this chat" disabled={loading} onClick={()=>reopen(conversation).catch(failure=>{if(failure.name!=='AbortError')setNotice(failure.message);})}><Icon name="refresh"/></button>}
          {session&&store==='database'&&<button className={`recent-chats ${savedChats?'active':''}`} data-testid="open-recent-chats" aria-expanded={savedChats} onClick={()=>{if(savedChats){closeHistory();return;}closeOverlays();setSavedChats(true);}}><Icon name="history"/>Recent chats{chatCount!==null&&<span className="count">{chatCount}</span>}</button>}
        </div>
        {store==='database'&&conversation&&pending&&!loading&&<div className="pending-bar" role="status"><Icon name="alert"/><span>A reply is still being prepared for this chat.</span>
          <span className="pending-actions"><button className="bank-chat-logout" disabled={loading} onClick={()=>reopen(conversation).catch(failure=>{if(failure.name!=='AbortError')setNotice(failure.message);})}>Refresh</button>
          <button className="bank-chat-logout" data-testid="cancel-pending-reply" onClick={cancelPending}>Cancel reply</button></span></div>}
        <section className="bank-chat-window" aria-label="Banking conversation">
          {booting&&<p className="chat-notice loading" role="status">Opening chat…</p>}
          {bootError&&<p className="chat-notice" role="alert">{bootError}</p>}
          {notice&&(noticeToast?<p key={noticeKey} className="chat-toast" role="status"><Icon name="check"/>{notice}</p>
            :<p key={noticeKey} className="chat-notice-bar" role="status"><Icon name="info"/><span>{notice}</span><button className="icon-button" aria-label="Dismiss message" onClick={()=>setNoticeText('')}><Icon name="close"/></button></p>)}
          <div ref={messageList} className={`bank-chat-messages ${messages.length?'has-messages':''}`} aria-live="polite" aria-relevant="additions text">
            {!messages.length&&<div className="bank-chat-welcome">{session?<><h1 className="welcome-signed-in" aria-busy={profileName===null}>{profileName===null?<><span className="skeleton-text heading" aria-hidden="true"/><span className="sr-only">How can I help today?</span></>:profileName?`Hi ${profileName.split(' ')[0]}, how can I help?`:'How can I help today?'}</h1><p className="chat-greeting" data-testid="chat-greeting">I'm the UBS Connect AI assistant. I give general information, not advice.</p><p>I answer from your own records and show where each answer comes from. If I can't help, I'll pass your question to your relationship manager.</p><ClientWelcome onDraft={draft} onNavigate={navigate}/></>
              :<><h1 className="welcome-signed-in">Hi, how can I help?</h1><p className="chat-greeting" data-testid="chat-greeting">I'm the UBS Connect AI assistant. I give general information, not advice.</p><p>I answer from the bank's published guides and show where each answer comes from. Sign in and I can answer from your own accounts too.</p><ClientWelcome guest onDraft={draft} onNavigate={navigate} onSignIn={onSignIn}/></>}</div>}
            {messages.map((m,i)=><div key={i} data-testid={`chat-message-${m.role}`} className={`bank-chat-message ${m.role}`} data-response-origin={m.response_origin} data-prompt-version={m.prompt_version}>
              {m.role==='assistant'&&<div className="bank-chat-avatar"><Icon name="spark"/></div>}
              <div className={`bank-chat-bubble ${m.error?'error':''}`}>
                {m.role==='assistant'&&<div className="answer-heading">Banking assistant{m.response_origin&&<small className="answer-origin">{m.response_origin==='ai'?'AI-generated':m.response_origin==='safety'?'Safety guidance':'Could not verify'}</small>}</div>}
                {meetingDraft(m)&&otherParts(m)&&<Answer message={otherParts(m)} onSource={showSource} loadSource={loadSource}/>}
                {meetingDraft(m)?<MeetingRequestCard draft={meetingDraft(m)} offer={m.offer} state={offerState(messages,i,now)} busy={loading||booting||!!bootError||!!pending}
                  onDecide={(decision,on)=>decideOffer(m,decision,on)} onAsk={text=>send(null,text)} onNavigate={navigate}
                  onMeetings={()=>navigate('meetings')}/>
                :<><Answer message={m} pendingText={loading?'Checking the available information…':''} onSource={showSource} loadSource={loadSource} link={pageLinkFor(m,i)}/>
                <OfferBlock offer={m.offer} state={offerState(messages,i,now)} busy={loading||booting||!!bootError||!!pending} onDecide={(decision,on)=>decideOffer(m,decision,on)}/></>}
                {!meetingDraft(m)&&ticketStep(m,i)}{inlineCardFor(m,i)}{!meetingDraft(m)&&nextQuestions(m,i)}
              </div>
            </div>)}
            {session&&mode==='personal'&&inlineRequest?.index===-1&&!inlineRequest.dismissed&&
              <div className="bank-chat-message assistant"><div className="bank-chat-avatar"><Icon name="spark"/></div><div className="bank-chat-bubble">
                <div className="answer-heading">Banking assistant</div>
                <InlineRequestCard key={inlineRequest.key} prefill={inlineRequest} onDismiss={()=>setInlineRequest(null)} onProposalChange={id=>{pendingInlineProposal.current=id;}} onSuccess={()=>setPageKey(k=>k+1)}/>
              </div></div>}
            {view==='chat'&&session&&mode==='personal'&&showCompliance&&<ComplianceWorkspace key="compliance-workspace" submissionReceipt={complianceReceipt} onSubmitted={setComplianceReceipt} onReviewLoaded={setComplianceReview} onSupport={prepareReviewSupport} onBusyChange={setComplianceBusy} chatBusy={loading||booting||!!bootError||!!pending} onAsk={(question,selection)=>send(null,question,selection)}/>}
          </div>
          <div className="composer-area"><form className="bank-chat-composer" onSubmit={send}>
            <textarea ref={composer} id="chat-composer" aria-label="Your message" rows={1} value={input} onChange={e=>{recall.current=null;setInput(e.target.value);}} disabled={loading||complianceBusy||booting||!!bootError||!!pending} maxLength={4000} placeholder={session?"Ask about your accounts, cards, plans or documents…":"Ask a banking question…"}
              onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();e.currentTarget.form.requestSubmit();}else recallSent(e);}}/>
            <button className="composer-send" data-testid="send-message" aria-label={loading?'Please wait':'Send'} title={loading?'Please wait':'Send'} disabled={loading||complianceBusy||booting||!!bootError||!!pending||!input.trim()}><Icon name="send"/></button>
          </form><div className="composer-note"><span><Icon name="shield"/>{DISCLAIMER}</span>
            {session&&chatTopics?.on&&<span data-testid="chat-topics-note">{chatTopicsNote(chatTopics.rm)} <a href="#sharing" onClick={event=>{event.preventDefault();navigate('sharing');setSharingFocus('chat_topics');}}>Manage</a></span>}
            <details className="chat-about"><summary>How chats are saved</summary><p>{store==='database'?(session?'Redacted chats are saved to your demo profile; reopen them from Recent chats. Starting a new chat keeps earlier ones.':'This browser keeps access to your redacted guest chat for 24 hours. Starting a new chat clears that access.'):'Chat history is temporary. Refreshing or starting a new chat clears it.'} Older messages may be left out of long chats.</p></details></div></div>
        </section>
        {footer}
      </main>
      {view==='chat'&&savedChats&&<SavedChats closing={historyClosing} current={conversation?.session_id} onLoaded={setChatCount} onClose={closeHistory} onOpen={async item=>{await reopen(item);navigate('chat');}}/>}
      {view!=='chat'&&<main className="app-page page-enter" key={view}>
        {!session&&<LockedPage page={view} onSignIn={onSignIn} onBack={()=>navigate('chat')}/>}
        {session&&view==='meetings'&&<MeetingsPage key={pageKey} rmName={rmIntro?.current?.name}/>}
        {session&&view==='overview'&&<OverviewPage key={pageKey} onNavigate={navigate} onRecords={()=>{closeOverlays();setRecordsOpen(true);}} onSupport={prepareSummary} onAsk={question=>{navigate('chat');draft(question);}} onRequestStatement={()=>requestStatement('')} onRaise={prefill=>{setTicketPrefill({...prefill,key:Date.now()});navigate('requests');}} focus={overviewFocus?.section}/>}
        {session&&view==='inbox'&&<InboxPage key={pageKey} onActions={openActions} links={{Cards:review,Support:requests,Appointments:{label:'Open Meetings',onSelect:()=>navigate('meetings')},Requests:requests,'My requests':requests,Relationship:requests,Compliance:compliance,Documents:compliance}}/>}
        {session&&view==='actions'&&<ActionsPanel key={pageKey}/>}
        {session&&view==='sharing'&&<SharingPage key={`${pageKey}-${sharingFocus||''}`} initial={sharingFocus} onReviewActions={openActions}/>}
        {session&&view==='requests'&&<TicketsPage key={pageKey} prefill={ticketPrefill} onReviewActions={openActions} onSupport={prepareSummary} rmName={rmIntro?.current?.name} initialTab={ticketPrefill?.tab||'open'}/>}
        {session&&view==='compliance'&&<CompliancePage key={pageKey} submissionReceipt={complianceReceipt} onSubmitted={setComplianceReceipt} onReviewLoaded={setComplianceReview} onSupport={prepareReviewSupport} onBusyChange={setComplianceBusy} chatBusy={loading||booting||!!bootError||!!pending} onAsk={(question,selection)=>{setComplianceOpen(true);navigate('chat');return send(null,question,selection);}}/>}
        {footer}
      </main>}
    </div>
    {summary!==null&&session&&<Panel label="Human support" closeLabel="Close summary" title="Talk to a person" subtitle={route&&route[3][1]!=='send'?(helpTopic==='fraud'?'Your relationship manager handles this as urgent.':'The cards and credit team decides card requests.'):rmIntro?.current?`Your relationship manager, ${displayName(rmIntro.current.name)}, will pick this up and reply to you.`:'Your relationship manager will pick this up and reply to you.'} onClose={()=>setSummary(null)}
      footer={<>{handoff.outcome?<div className="panel-actions"><button className="bank-chat-logout" onClick={()=>setSummary(null)}>Close</button><button className="primary-button" data-testid="handoff-open-requests" onClick={()=>{setSummary(null);navigate('requests');}}>See my requests<Icon name="arrow"/></button></div>:<div className="panel-actions"><button className="bank-chat-logout" data-testid="cancel-handoff" disabled={handoff.saving} onClick={()=>setSummary(null)}>Cancel</button>
        {route?.[2]&&<button className="bank-chat-logout" data-testid="handoff-route-secondary" onClick={()=>followRoute(route[2][1])}>{route[2][0]}</button>}
        <button className="primary-button" data-testid={route&&route[3][1]!=='send'?'handoff-route':'approve-handoff'} disabled={handoff.saving}
          onClick={()=>route?followRoute(route[3][1]):sendHandoff()}>{route?route[3][0]:'Send to my relationship manager'}</button></div>}</>}>
      <label className="form-field">What do you need help with?<select data-testid="handoff-topic" value={helpTopic} disabled={handoff.saving||!!handoff.outcome} onChange={e=>changeTopic(e.target.value)}>{HELP_TOPICS.map(([value,text])=><option key={value} value={value}>{text}</option>)}</select></label>
      {route&&<div className="handoff-route" data-testid="handoff-route-info"><strong>{route[0]}</strong><p>{route[1]}</p></div>}
      {(!route||route[3][1]==='send')&&!supportScope&&<label className="sp-attach"><input type="checkbox" checked={attachSummary} disabled={handoff.saving||!!handoff.outcome} onChange={e=>setAttachSummary(e.target.checked)}/><span><strong>Attach a short summary of this chat</strong><small>Only the topics you asked about, never passwords, OTPs or card numbers.</small></span></label>}
      {(!route||route[3][1]==='send')&&<label className="form-field rm-message">Your message to your relationship manager
        <textarea data-testid="handoff-message" rows={5} value={rmMessage} maxLength={supportScope?480:1000} disabled={handoff.saving||!!handoff.outcome}
          onChange={e=>{rmEdited.current=true;setRmMessage(e.target.value);}}/>
        <small>Drafted for you. Change anything before you send it.</small>
        <span className="sr-only" data-testid="handoff-summary">{handoffText}</span></label>}
      {(supportScope||attachSummary)&&handoffText&&<details data-testid="handoff-context"><summary>What’s attached</summary><p>{handoffText}</p></details>}
      <p className="security-note"><Icon name="shield"/>Never share your PIN, password or OTP. Lost card or fraud? Freeze your card on Home or call the number on the back of it.</p>
      {handoff.saving&&<p className="loading" role="status">Sending…</p>}
      {handoff.error&&<p role="alert">{handoff.error}</p>}
      {handoff.outcome&&<div role="status" data-testid="handoff-receipt"><p>{handoff.outcome.receipt.result.created?'Sent to your relationship manager.':'Your earlier question is still open with your relationship manager.'} You can follow it in Requests.</p></div>}
    </Panel>}
    {summary!==null&&!session&&<Panel label="Human support" closeLabel="Close summary" title="Prepare a support summary" subtitle="Draft a summary you can copy into your bank’s official support channel." onClose={()=>setSummary(null)}><p>This demo has no staffed support queue. For a real issue, use your bank’s official app or website. This draft is not sent anywhere.</p><textarea aria-label="Support summary" rows={7} value={summary} onChange={e=>{setSummary(e.target.value);setApproved(false);setCopied(false);}}/><label className="review-summary"><input type="checkbox" checked={approved} onChange={e=>setApproved(e.target.checked)}/> I reviewed the summary and removed private details</label><div className="panel-actions"><button className="primary-button" disabled={!approved} onClick={async()=>{try{setCopyError('');await navigator.clipboard.writeText(redact(summary));setCopied(true);}catch{setCopyError('Copy unavailable. Select and copy the draft manually.');}}}>Copy summary</button></div>{copied&&<p role="status">Copied. No ticket was sent.</p>}{copyError&&<p role="alert">{copyError}</p>}</Panel>}
    {article&&<Panel label="Source details" closeLabel="Close source" title={article.title} onClose={()=>setArticle(null)}><p className="source-notice">{article.notice}</p><p>{article.content}</p><p className="source-publisher">Publisher: {article.publisher}</p>
      {article.reference_url&&<p><a href={article.reference_url} target="_blank" rel="noopener noreferrer">Read the original source <span aria-hidden="true">↗</span></a></p>}
      {article.checked_on&&<p>Source checked: {article.checked_on}. Review due: {article.review_due}.</p>}
      {article.current===false&&<p role="status">This source needs review and is not used for new answers.</p>}
      <small>Version {article.version}</small></Panel>}
    {recordsOpen&&<RecordsPanel onClose={()=>setRecordsOpen(false)} onReviewActions={openActions}/>}
  </div>;
}
