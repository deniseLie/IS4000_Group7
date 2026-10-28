import {useEffect,useRef,useState} from 'react';
import {supabase} from '../lib/supabase';
import {checkIsFresh,findingText,loadTemplates,loadWorkspace,primaryStatus,runPrecheck,submitDocument,templateFor} from '../lib/compliance';
import ChecklistPreparation from './ChecklistPreparation';
import Icon from './Icon';
import {formatDate,formatDateTime} from '../lib/format';
import {cacheOwner,cached,remember} from '../lib/cache';
import DocumentUpload from './DocumentUpload';

// What each kind of document must show, with a made-up example (GET /documents/templates; public, synthetic).
function DocumentGuide({template}){
 if(!template)return null;
 return <div className="cw-guide" data-testid="document-guide"><strong>Your file must show</strong>
  <ul>{template.must_show.map(line=><li key={line}>{line}</li>)}</ul>
  <details className="cw-more"><summary>See a made-up example</summary><div className="cw-example" aria-label={`Example: ${template.title}`}>{template.example.map(line=><p key={line}>{line}</p>)}</div></details>
  {template.tips?.length>0&&<p className="rm-small">{template.tips.join(' ')}</p>}</div>;
}

const sentence=value=>{const text=value.replaceAll('_',' ');return text[0].toUpperCase()+text.slice(1);};

export default function ComplianceWorkspace({onPage=false,onAsk,chatBusy=false,onBusyChange,onSupport,onReviewLoaded,submissionReceipt=null,onSubmitted}){
 // A return visit shows the last loaded review at once (actions stay disabled while busy); only a fresh read sets the server clock.
 const [workspace,setWorkspace]=useState(()=>cached('compliance')),[error,setError]=useState(''),[busy,setBusy]=useState(false),[receipt,setReceipt]=useState(null),[now,setNow]=useState(Date.now());
 const [templates,setTemplates]=useState([]);
 const mounted=useRef(false),active=useRef(null),serverClock=useRef(null),owner=useRef(cacheOwner());
 function snapshot(result){serverClock.current={server:Date.parse(result.as_of),local:performance.now()};setNow(serverClock.current.server);setWorkspace(remember('compliance',result,owner.current));onReviewLoaded?.(result.review?.case_id??null);}
 async function token(){const {data,error}=await supabase.auth.getSession();if(error)throw error;if(!data.session)throw new Error('Sign in again to view your compliance review.');return data.session.access_token;}
 async function refresh(){
  active.current?.abort();const controller=new AbortController();active.current=controller;setBusy(true);setError('');
  try{const result=await loadWorkspace(await token(),controller.signal);if(mounted.current&&!controller.signal.aborted)snapshot(result);}
  catch(failure){if(mounted.current&&!controller.signal.aborted){setError(failure.message);setWorkspace(null);}}
  finally{if(mounted.current&&!controller.signal.aborted)setBusy(false);}
 }
 useEffect(()=>{const controller=new AbortController();loadTemplates(controller.signal).then(setTemplates).catch(()=>{});return ()=>controller.abort();},[]);
 useEffect(()=>{onBusyChange?.(busy);return ()=>onBusyChange?.(false);},[busy,onBusyChange]);
 useEffect(()=>{mounted.current=true;refresh();const timer=setInterval(()=>{const base=serverClock.current;if(base)setNow(base.server+performance.now()-base.local);},1000);return ()=>{mounted.current=false;active.current?.abort();clearInterval(timer);};},[]);
 async function checkAndSubmit(documentId,recheck=false){
  setBusy(true);setError('');setReceipt(null);
  try{
   const access=await token(),report=await runPrecheck(documentId,access,undefined,recheck);
   if(report.status==='no_issues'){
    const result=await submitDocument(documentId,access);
    if(result.receipt?.outcome!=='succeeded')throw new Error('The item or file changed. Your document was not submitted. Reload before retrying.');
    if(mounted.current){setReceipt(result.receipt.receipt_id);onSubmitted?.({case_id:report.case_id,receipt_id:result.receipt.receipt_id});}
   }
   const result=await loadWorkspace(access);if(mounted.current)snapshot(result);
   if(mounted.current&&onAsk)await onAsk('Please explain the recorded preliminary check for the document I just checked, including any corrections and its actual submission status.',{kind:'document',case_id:report.case_id,item_id:report.item_id,document_id:report.document_id,check_id:report.check_id});
  }catch(failure){if(mounted.current)setError(failure.message+' The file may already be recorded. Reload before retrying.');}
  finally{if(mounted.current)setBusy(false);}
 }
 const review=workspace?.review;
 const displayedReceipt=receipt||(submissionReceipt&&submissionReceipt.case_id===review?.case_id?submissionReceipt.receipt_id:null);
 const stale=review&&now-Date.parse(review.status_updated_at)>workspace.policy.status_stale_days*86400000;
 const closed=review&&['completed','closed'].includes(review.status);
 const DONE=['accepted','submitted_awaiting_review'];
 const tone=item=>item.submission_status==='accepted'?'ok':item.submission_status==='submitted_awaiting_review'?'wait':item.submission_status==='rejected'?'bad':'todo';
 const items=review?.requested_items||[],finished=items.filter(item=>DONE.includes(item.submission_status)).length;
 const pending=items.filter(item=>!DONE.includes(item.submission_status)),withBank=items.filter(item=>DONE.includes(item.submission_status));
 const markTone=item=>item.submission_status==='rejected'?'bad':item.client_mark==='prepared'?'ok':'todo';
 const icon=item=>/address/i.test(item.label)?'pin':/statement/i.test(item.label)?'document':/tax/i.test(item.label)?'bank':'card';
 const help=selection=>onSupport&&<button className="bank-chat-logout" disabled={busy||chatBusy} onClick={()=>onSupport(selection)}><Icon name="support"/>{selection.request_id?'Get help with this request':selection.item_id?'Get help with this document':'Get help with this review'}</button>;
 return <section className={`compliance-workspace cw ${onPage?'cw-page':''}`} data-testid="compliance-workspace" aria-label="Your compliance review">
  <div className="cw-top"><div className="cw-tags">{review?<><span className="cw-notice-tag"><span className="dot"/>Formal compliance notice</span><code>Case ref: #{review.case_id.replaceAll('-','').slice(-8).toUpperCase()}</code></>:onPage?null:<h3>Your compliance review</h3>}</div>
   <button className="bank-chat-logout" disabled={busy} onClick={refresh}><Icon name="refresh"/>Refresh</button></div>
  {onPage&&<header className="cw-title"><h1>Your compliance review</h1><p>Upload what the bank asked for. Each file gets a preliminary check before it is submitted.</p></header>}
  {!onPage&&review&&<h3 className="cw-chat-title">Your compliance review</h3>}
  {busy&&<p className="cw-busy loading" role="status">Checking recorded progress…</p>}
  {busy&&!workspace&&<div className="loading-list" aria-hidden="true"><span className="skeleton-block tall"/><span className="skeleton-card large"/><span className="skeleton-card large"/></div>}
  {error&&<p role="alert" data-testid="compliance-workspace-error">{error}</p>}
  {workspace&&!review&&<div className="cw-none" data-testid="compliance-none"><span><Icon name="check"/></span><div><h4>No review needed</h4><p>There is nothing for you to do right now. If the bank needs documents or information, you will see it here and in your Inbox.</p></div></div>}
  {review&&<>
   <article className="cw-card cw-overview">
    <div className="cw-card-head"><h3>Review progress <span className={`cw-pill ${closed?'ok':'wait'}`}>{closed?'Closed':'In progress'}</span></h3><span className="cw-asof">Retrieved {formatDateTime(workspace.as_of)}</span></div>
    <div className="cw-summary">
     <div><h4>{review.title}</h4><p className="cw-meta"><span className="cw-chip">{sentence(review.status)}</span>
      {review.due_date&&<span><Icon name="calendar"/>Due <strong>{formatDate(review.due_date)}</strong></span>}<span>· Updated <time dateTime={review.status_updated_at}>{formatDateTime(review.status_updated_at)}</time></span></p></div>
     <div className="cw-progress" aria-label={`${finished} of ${items.length} documents with the bank`}><span>Documents with bank</span><strong>{finished} of {items.length}</strong>
      <div className="cw-bar"><span style={{width:`${items.length?finished/items.length*100:0}%`}}/></div></div>
    </div>
    <details className="cw-notice"><summary>Read official notice text</summary><p>{review.notice_text}</p></details>
    {stale&&<p className="cw-warn" data-testid="compliance-stale">This status is more than {workspace.policy.status_stale_days} days old. Reload or contact support to confirm progress.</p>}
    <div className="cw-split compliance-progress">
     <section><h4 className="cw-label"><Icon name="document"/>Your required actions</h4>
      {pending.length?<ul className="cw-actions">{pending.map((item,index)=><li key={item.item_id} className={index===0?'current':''}><span className="dot"/><div><strong>{item.label}</strong><small>{review.due_date?`Case deadline ${review.due_date}`:'No case deadline recorded'}</small></div><span className={`cw-pill ${markTone(item)}`}>{primaryStatus(item)}</span></li>)}</ul>
       :<p className="cw-ok"><Icon name="check"/>Every requested document is with the bank.</p>}
     </section>
     <section className="cw-bank"><h4 className="cw-label"><Icon name="bank"/>With the bank</h4>
      <p>Only recorded bank decisions are shown. A preliminary automated check validates your file against the notice, but never approves the review on its own.</p>
      {withBank.length>0&&<ul className="cw-actions">{withBank.map(item=><li key={item.item_id}><span className="dot ok"/><div><strong>{item.label}</strong></div><span className={`cw-pill ${tone(item)}`}>{primaryStatus(item)}</span></li>)}</ul>}
      <div className="cw-bank-foot"><span><span className="dot"/>{withBank.length?`${withBank.length} awaiting or decided by the bank`:'Nothing submitted yet'}</span><code>{pending.length} still to send</code></div>
     </section>
    </div>
   </article>
   {review.info_requests.filter(item=>item.status==='open').map(item=><article key={item.request_id} className="cw-alert">
    <span className="cw-alert-icon" aria-hidden="true">!</span>
    <div className="cw-alert-body"><div className="cw-alert-head"><span className="eyebrow">The bank asked you</span><span className="cw-deadline">Request deadline: {item.due_date||'Not recorded'}</span></div>
     <p><strong>{item.question}</strong></p><p>{item.instructions||'Submission instructions are not recorded; contact support.'}</p>
     <div className="panel-actions">{onAsk&&<button className="primary-button" disabled={busy||chatBusy} onClick={()=>onAsk('Please explain the selected recorded request for additional information and where I should respond.',{kind:'information_request',case_id:review.case_id,request_id:item.request_id})}>Explain this request<Icon name="arrow"/></button>}
      {help({review_case_id:review.case_id,request_id:item.request_id})}</div></div></article>)}
   <div className="cw-head"><div><h4>Documents to upload</h4><p className="rm-small">PDF works best (DOCX, PNG or JPEG also accepted). Each upload gets a quick check; if everything is there it goes to the bank for review.</p></div>{help({review_case_id:review.case_id})}</div>
   <ul className="compliance-items">{items.map(item=>{
    const current=workspace.current_documents[item.item_id],report=workspace.checks.find(report=>report.document_id===current),fresh=report&&checkIsFresh(report,now),rule=workspace.rules[item.item_id];
    const open=!closed&&item.submission_status!=='accepted';
    return <li key={item.item_id} data-testid="compliance-item" data-item-id={item.item_id} className={`cw-item ${tone(item)}`}>
     <div className="cw-item-head"><h4>{item.label}</h4><p data-testid="compliance-item-status" className={`cw-pill ${DONE.includes(item.submission_status)?tone(item):markTone(item)}`}><strong>{primaryStatus(item)}</strong></p><span className="cw-item-icon"><Icon name={icon(item)}/></span></div>
     <p className="cw-instructions">{item.instructions||'Instructions unavailable. Contact support rather than guessing.'}</p>
     {open&&<DocumentGuide template={templateFor(item,templates)}/>}
     {rule?<details className="cw-more"><summary>How the check works</summary><p>Each upload gets a quick automatic check against the list above. It looks for the details only; it does not confirm the document is genuine, and only the bank can accept it.</p><p>A check result stays valid for {workspace.policy.validity_hours} hours.</p></details>:<p>Automatic checks are not set up for this item. Use Get help with this document.</p>}
     {report&&<div data-testid="precheck-result" data-precheck-status={fresh?report.status:'expired'} className={`cw-check ${fresh?report.status:'expired'}`}>
      <strong>Quick check – not bank approval</strong><p>{!fresh?'This preliminary check has expired. Check the current upload again before submitting.':report.status==='no_issues'?'Everything we look for is there.':report.status==='issues_found'?'Please fix these before submitting:':'We could not check this file.'}</p>
      <ul>{fresh&&report.findings.map((finding,index)=><li key={index}>{findingText(finding,rule)}</li>)}</ul>
      <p className="rm-small">Checked {formatDateTime(report.checked_at)} · valid until {report.expires_at?formatDateTime(report.expires_at):'the recorded expiry'}.</p>
      {onAsk&&<button className="bank-chat-logout" disabled={busy||chatBusy} onClick={()=>onAsk('Explain the selected preliminary document check findings and how to fix them.',{kind:'document',case_id:review.case_id,item_id:item.item_id,document_id:current,check_id:report.check_id})}>Explain findings in chat</button>}
     </div>}
     {open&&(DONE.includes(item.submission_status)?<details className="cw-more"><summary>Upload a replacement</summary><DocumentUpload key={current||item.item_id} review={review} itemId={item.item_id} onRegistered={checkAndSubmit} disabled={busy||chatBusy}/></details>
      :<DocumentUpload key={current||item.item_id} review={review} itemId={item.item_id} onRegistered={checkAndSubmit} disabled={busy||chatBusy}/>)}
     <div className="cw-item-foot">
      {!closed&&!DONE.includes(item.submission_status)?<ChecklistPreparation key={`${item.item_id}:${item.client_marked_at}`} review={review} item={item} onSaved={refresh}/>:<span/>}
      <div className="panel-actions">{current&&open&&<button className="cw-subtle" data-testid="check-current-upload" disabled={busy||chatBusy||!rule} onClick={()=>checkAndSubmit(current,true)}>Check and submit current upload</button>}
      {help({review_case_id:review.case_id,item_id:item.item_id})}</div>
     </div>
    </li>;
   })}</ul>
   {displayedReceipt&&<p role="status" data-testid="submission-receipt" className="cw-receipt">Submitted – awaiting review. Receipt: <code>{displayedReceipt}</code>. This is not bank acceptance.</p>}
  </>}
 </section>;
}
