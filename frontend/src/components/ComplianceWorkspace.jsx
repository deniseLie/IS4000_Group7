import {useEffect,useRef,useState} from 'react';
import {supabase} from '../lib/supabase';
import {checkIsFresh,findingText,loadWorkspace,primaryStatus,runPrecheck,submitDocument} from '../lib/compliance';
import ChecklistPreparation from './ChecklistPreparation';
import Icon from './Icon';
import DocumentUpload from './DocumentUpload';

export default function ComplianceWorkspace({onPage=false,onAsk,chatBusy=false,onBusyChange,onSupport,onReviewLoaded,submissionReceipt=null,onSubmitted}){
 const [workspace,setWorkspace]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[receipt,setReceipt]=useState(null),[now,setNow]=useState(Date.now());
 const mounted=useRef(false),active=useRef(null),serverClock=useRef(null);
 function snapshot(result){serverClock.current={server:Date.parse(result.as_of),local:performance.now()};setNow(serverClock.current.server);setWorkspace(result);onReviewLoaded?.(result.review?.case_id??null);}
 async function token(){const {data,error}=await supabase.auth.getSession();if(error)throw error;if(!data.session)throw new Error('Sign in again to view your compliance review.');return data.session.access_token;}
 async function refresh(){
  active.current?.abort();const controller=new AbortController();active.current=controller;setBusy(true);setError('');
  try{const result=await loadWorkspace(await token(),controller.signal);if(mounted.current&&!controller.signal.aborted)snapshot(result);}
  catch(failure){if(mounted.current&&!controller.signal.aborted){setError(failure.message);setWorkspace(null);}}
  finally{if(mounted.current&&!controller.signal.aborted)setBusy(false);}
 }
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
 return <section className="compliance-workspace cw" data-testid="compliance-workspace" aria-label="Your compliance review">
  <div className="panel-heading"><h3>{onPage?'Review progress':'Your compliance review'}</h3><button className="bank-chat-logout" disabled={busy} onClick={refresh}>Reload review</button></div>
  {busy&&<p role="status">Checking recorded progress…</p>}
  {error&&<p role="alert" data-testid="compliance-workspace-error">{error}</p>}
  {workspace&&!review&&<div className="cw-none" data-testid="compliance-none"><span><Icon name="check"/></span><div><h4>No review needed</h4><p>There is nothing for you to do right now. If the bank needs documents or information, you will see it here and in your Inbox.</p></div></div>}
  {review&&<>
   <div className="cw-summary">
    <div><h4>{review.title}</h4><p className="cw-meta"><span className={`cw-pill ${closed?'ok':'wait'}`}>{review.status.replaceAll('_',' ')}</span>
     {review.due_date&&<span>Due <strong>{review.due_date}</strong></span>}<span>Updated <time dateTime={review.status_updated_at}>{new Date(review.status_updated_at).toLocaleDateString()}</time></span></p></div>
    <div className="cw-progress" aria-label={`${finished} of ${items.length} documents with the bank`}><strong>{finished} of {items.length}</strong><span>documents with the bank</span>
     <div className="cw-bar"><span style={{width:`${items.length?finished/items.length*100:0}%`}}/></div></div>
   </div>
   <details className="cw-notice"><summary>Read your notice</summary><p>{review.notice_text}</p></details>
   {stale&&<p className="cw-warn" data-testid="compliance-stale">This status is more than {workspace.policy.status_stale_days} days old. Reload or contact support to confirm progress.</p>}
   <div className="compliance-progress"><section><h4>Your action</h4>
    <ul>{items.filter(item=>!DONE.includes(item.submission_status)).map(item=><li key={item.item_id}>{item.label}: {primaryStatus(item)}{review.due_date&&` · Case deadline ${review.due_date}`}</li>)}</ul>
    {review.info_requests.filter(item=>item.status==='open').map(item=><article key={item.request_id} className="cw-alert"><span className="eyebrow">The bank asked you</span><p><strong>{item.question}</strong></p><p>{item.instructions||'Submission instructions are not recorded; contact support.'}</p><p className="rm-small">Request deadline: {item.due_date||'Not recorded'}.</p>
     <div className="panel-actions">{onAsk&&<button className="bank-chat-logout" disabled={busy||chatBusy} onClick={()=>onAsk('Please explain the selected recorded request for additional information and where I should respond.',{kind:'information_request',case_id:review.case_id,request_id:item.request_id})}>Explain this request</button>}
     {onSupport&&<button className="bank-chat-logout" disabled={busy||chatBusy} onClick={()=>onSupport({review_case_id:review.case_id,request_id:item.request_id})}>Get help with this request</button>}</div></article>)}
   </section><section><h4>With the bank</h4><ul>{items.filter(item=>DONE.includes(item.submission_status)).map(item=><li key={item.item_id}>{item.label}: {primaryStatus(item)}</li>)}</ul><p className="rm-small">Only recorded bank decisions are shown. A preliminary check never approves the review.</p></section></div>
   <div className="cw-head"><h4>Documents to upload</h4>{onSupport&&<button className="bank-chat-logout" disabled={busy||chatBusy} onClick={()=>onSupport({review_case_id:review.case_id})}>Get help with this review</button>}</div>
   <p className="rm-small">Fictional files only (PDF, DOCX, PNG or JPEG). Each upload is checked against your notice; a passing check submits it for bank review.</p>
   <ul className="compliance-items">{items.map(item=>{
    const current=workspace.current_documents[item.item_id],report=workspace.checks.find(report=>report.document_id===current),fresh=report&&checkIsFresh(report,now),rule=workspace.rules[item.item_id];
    const open=!closed&&item.submission_status!=='accepted';
    return <li key={item.item_id} data-testid="compliance-item" data-item-id={item.item_id} className={`cw-item ${tone(item)}`}>
     <div className="cw-item-head"><h4>{item.label}</h4><p data-testid="compliance-item-status" className={`cw-pill ${tone(item)}`}><strong>{primaryStatus(item)}</strong></p></div>
     <p className="cw-instructions">{item.instructions||'Instructions unavailable. Contact support rather than guessing.'}</p>
     {rule?<details className="cw-more"><summary>Requirements from your notice</summary><p>{item.instructions||item.label}</p>{item.check_guidance&&<p>Approved checking guidance: {item.check_guidance}</p>}<p>Results stay valid for {workspace.policy.validity_hours} hours; period coverage uses {workspace.policy.statement_period_mode.replaceAll('_',' ')}.</p><p>Text from your file is read by the configured AI provider for this preliminary check. Authenticity, identity and signatures are not verified.</p></details>:<p>No assessment rules are configured. Contact support.</p>}
     {report&&<div data-testid="precheck-result" data-precheck-status={fresh?report.status:'expired'} className={`cw-check ${fresh?report.status:'expired'}`}>
      <strong>PRELIMINARY check – not formal approval</strong><p>{!fresh?'This preliminary check has expired. Check the current upload again before submitting.':report.status==='no_issues'?'No issues found against the recorded notice requirements.':report.status==='issues_found'?'Please correct these notice requirement issues:':'Unable to assess reliably. No pass or fail is given.'}</p>
      <ul>{fresh&&report.findings.map((finding,index)=><li key={index}>{findingText(finding,rule)}</li>)}</ul>
      <p className="rm-small">Checked {new Date(report.checked_at).toLocaleString()} · valid until {report.expires_at?new Date(report.expires_at).toLocaleString():'the recorded expiry'}.</p>
      {onAsk&&<button className="bank-chat-logout" disabled={busy||chatBusy} onClick={()=>onAsk('Explain the selected preliminary document check findings and how to fix them.',{kind:'document',case_id:review.case_id,item_id:item.item_id,document_id:current,check_id:report.check_id})}>Explain findings in chat</button>}
     </div>}
     {open&&(DONE.includes(item.submission_status)?<details className="cw-more"><summary>Upload a replacement</summary><DocumentUpload key={current||item.item_id} review={review} itemId={item.item_id} onRegistered={checkAndSubmit} disabled={busy||chatBusy}/></details>
      :<DocumentUpload key={current||item.item_id} review={review} itemId={item.item_id} onRegistered={checkAndSubmit} disabled={busy||chatBusy}/>)}
     <div className="cw-item-foot">
      {!closed&&!DONE.includes(item.submission_status)&&<ChecklistPreparation key={`${item.item_id}:${item.client_marked_at}`} review={review} item={item} onSaved={refresh}/>}
      <div className="panel-actions">{current&&open&&<button className="bank-chat-logout" data-testid="check-current-upload" disabled={busy||chatBusy||!rule} onClick={()=>checkAndSubmit(current,true)}>Check and submit current upload</button>}
      {onSupport&&<button className="bank-chat-logout" disabled={busy||chatBusy} onClick={()=>onSupport({review_case_id:review.case_id,item_id:item.item_id})}>Get help with this document</button>}</div>
     </div>
    </li>;
   })}</ul>
   {displayedReceipt&&<p role="status" data-testid="submission-receipt" className="cw-receipt">Submitted – awaiting review. Receipt: <code>{displayedReceipt}</code>. This is not bank acceptance.</p>}
   <p className="rm-small">Retrieved {new Date(workspace.as_of).toLocaleString()}. Case deadline: {review.due_date||'Not recorded'}.</p>
  </>}
 </section>;
}
