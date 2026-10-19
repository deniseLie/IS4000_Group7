import {parseWorkflow} from './workflows.js';
const API_URL=import.meta.env?.VITE_API_URL || '/api';
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MODES=['public','personal'],LANGUAGES=['English','Chinese','Malay','Tamil'];

function invalid() {return new Error('The saved-chat response could not be verified. Please retry.');}
function identifier(value) {if(typeof value!=='string'||!UUID.test(value))throw invalid();return value;}
function nullableId(value) {return value===null?null:identifier(value);}
function timestamp(value) {
  if(typeof value!=='string'||!/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(value)||!Number.isFinite(Date.parse(value)))throw invalid();
  return value;
}
function revision(value) {if(!Number.isSafeInteger(value)||value<0)throw invalid();return value;}
function text(value) {if(typeof value!=='string')throw invalid();return value;}
function source(value) {
  if(!value||!value.id||!value.title||!value.version||!(value.url===null||typeof value.url==='string'&&/^\/knowledge\/[a-z0-9/#-]+$/i.test(value.url)))throw invalid();
  if(value.reference_url!==null&&value.reference_url!==undefined) {
    const url=new URL(value.reference_url);
    if(url.protocol!=='https:'||!['www.moneysense.gov.sg','www.abs.org.sg'].includes(url.hostname)||url.username||url.password)throw invalid();
  }
  return {id:text(value.id),title:text(value.title),version:text(value.version),url:value.url};
}
export function parseSavedAnswer(value) {
  if(!value||!['ai','safety','validation'].includes(value.response_origin)||!Array.isArray(value.sources)||typeof value.needs_human!=='boolean')throw invalid();
  const result={answer:text(value.answer),disclaimer:text(value.disclaimer),response_origin:value.response_origin,
    source_check:text(value.source_check),needs_human:value.needs_human,prompt_version:value.prompt_version,sources:value.sources.map(source)};
  if(value.sections!==null&&value.sections!==undefined) {
    if(!Array.isArray(value.sections))throw invalid();
    result.sections=value.sections.map(section=>{
      if(!section||!['retail','fraud','compliance','document','relationship','smalltalk'].includes(section.domain)||!['P0','P1','P2','P3'].includes(section.priority)||!Array.isArray(section.sources)||!['ai','safety','validation'].includes(section.response_origin))throw invalid();
      return {task_id:text(section.task_id),domain:section.domain,priority:section.priority,answer:text(section.answer),
        response_origin:section.response_origin,prompt_version:section.prompt_version,sources:section.sources.map(source)};
    });
  }
  return result;
}
export function parseSession(value) {
  if(!value||!MODES.includes(value.mode)||!LANGUAGES.includes(value.language))throw invalid();
  return {session_id:identifier(value.session_id),mode:value.mode,language:value.language,revision:revision(value.revision),
    active_turn_id:nullableId(value.active_turn_id),started_at:timestamp(value.started_at),last_active_at:timestamp(value.last_active_at),
    expires_at:value.expires_at===null?null:timestamp(value.expires_at)};
}
export function parseMetadata(value) {
  if(!value)throw invalid();
  return {conversation_id:identifier(value.conversation_id),revision:revision(value.revision),active_turn_id:nullableId(value.active_turn_id)};
}
export function parseHistory(value) {
  if(!value||!Array.isArray(value.messages)||value.messages.length>60||typeof value.history_trimmed!=='boolean')throw invalid();
  const session=parseSession(value.session);
  let previous=0;
  const messages=value.messages.map(item=>{
    if(!item||!['user','assistant'].includes(item.role)||!Number.isSafeInteger(item.sequence)||item.sequence<=previous)throw invalid();
    previous=item.sequence;identifier(item.message_id);timestamp(item.created_at);
    const content=text(item.redacted_text);
    if(!content||content.length>20008)throw invalid();
    if(item.role==='user') {if(item.payload!==null||content.length>4000)throw invalid();return {role:'user',content};}
    if(item.payload?.error) {
      if(!['assistant_unavailable','cancelled','interrupted'].includes(item.payload.error.code)||item.payload.error.message!==content)throw invalid();
      return {role:'assistant',content,error:true};
    }
    const answer=parseSavedAnswer(item.payload);
    if(answer.answer!==content)throw invalid();
    return {role:'assistant',content,...answer};
  });
  const active=value.active_turn;
  if(active===null?session.active_turn_id!==null:!active||active.turn_id!==session.active_turn_id||active.session_id!==session.session_id||active.state!=='processing')throw invalid();
  const workflow=value.workflow===null||value.workflow===undefined?null:parseWorkflow(value.workflow);
  if(workflow&&(workflow.session_id!==session.session_id||workflow.turn_id!==session.active_turn_id))throw invalid();
  return {session,messages,history_trimmed:value.history_trimmed,active_turn_id:session.active_turn_id,workflow};
}

function activeKey(userId) {return `demobank.active.${userId||'guest'}`;}
export function readActive(userId) {
  const stored=localStorage.getItem(activeKey(userId));
  if(stored===null)return null;
  let value;
  try {value=JSON.parse(stored);}
  catch(error) {if(error instanceof SyntaxError)throw new Error('The saved-chat access record is invalid. Start a new chat.',{cause:error});throw error;}
  if(!value||!(value.guest_token===null||typeof value.guest_token==='string'&&/^[A-Za-z0-9_-]{43}$/.test(value.guest_token)))throw new Error('The saved-chat access record is invalid. Start a new chat.');
  return {session_id:identifier(value.session_id),guest_token:value.guest_token};
}
export function writeActive(userId,access) {localStorage.setItem(activeKey(userId),JSON.stringify({session_id:access.session_id,guest_token:access.guest_token}));}
export function clearActive(userId) {localStorage.removeItem(activeKey(userId));}
export function newCustomerAccess() {return {session_id:crypto.randomUUID(),guest_token:null};}
export function newGuestAccess() {
  const token=btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(32)))).replaceAll('+','-').replaceAll('/','_').replaceAll('=','');
  return {session_id:crypto.randomUUID(),guest_token:token};
}
function accessHeaders(headers,access) {return access.guest_token?{...headers,'X-Conversation-Token':access.guest_token}:headers;}
async function responseBody(response) {
  let value;
  try {value=await response.json();}
  catch(error) {if(error instanceof SyntaxError)throw invalid();throw error;}
  if(!response.ok)throw new Error(typeof value?.error?.message==='string'&&value.error.message.trim()?value.error.message:`The saved-chat request failed (HTTP ${response.status}). Please retry.`);
  return value;
}
export async function capabilities(signal) {
  const value=await responseBody(await fetch(`${API_URL}/capabilities`,{signal}));
  if(!['off','database'].includes(value.conversation_store))throw invalid();
  return value.conversation_store;
}
export async function createConversation(access,mode,language,headers,signal) {
  const value=parseSession(await responseBody(await fetch(`${API_URL}/conversations`,{method:'POST',
    headers:{...accessHeaders(headers,access),'Content-Type':'application/json'},body:JSON.stringify({session_id:access.session_id,mode,language}),signal})));
  if(value.session_id!==access.session_id)throw invalid();
  return value;
}
export async function loadConversation(access,headers,signal) {
  const history=parseHistory(await responseBody(await fetch(`${API_URL}/conversations/${access.session_id}`,{headers:accessHeaders(headers,access),signal})));
  if(history.session.session_id!==access.session_id)throw invalid();
  return history;
}
export async function listConversations(headers,signal) {
  const value=await responseBody(await fetch(`${API_URL}/conversations`,{headers,signal}));
  if(!Array.isArray(value))throw invalid();
  return value.map(parseSession);
}
export async function cancelConversationTurn(access,turnId,headers,signal) {
  await responseBody(await fetch(`${API_URL}/conversations/${access.session_id}/turns/${identifier(turnId)}/cancel`,{
    method:'POST',headers:accessHeaders(headers,access),signal}));
  return loadConversation(access,headers,signal);
}
