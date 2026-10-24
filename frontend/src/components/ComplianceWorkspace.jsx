import {useEffect,useRef,useState} from 'react';
import {supabase} from '../lib/supabase';
import {checkIsFresh,findingText,loadWorkspace,primaryStatus,runPrecheck,submitDocument} from '../lib/compliance';
import ChecklistPreparation from './ChecklistPreparation';
import DocumentUpload from './DocumentUpload';

export default function ComplianceWorkspace({onAsk,chatBusy=false,onBusyChange,onSupport,onReviewLoaded,submissionReceipt=null,onSubmitted}){
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
 return <section className="compliance-workspace" data-testid="compliance-workspace" aria-label="Your compliance review">
  <div className="panel-heading"><h3>Your compliance review</h3><button className="bank-chat-logout" disabled={busy} onClick={refresh}>Reload review</button></div>
  {busy&&<p role="status">Checking recorded progress… Submitted operations continue if you close this view.</p>}
  {error&&<p role="alert" data-testid="compliance-workspace-error">{error}</p>}
  {workspace&&!review&&<p>No client-visible review is recorded. Contact your bank if your notice is missing here.</p>}
  {review&&<>
   <h4>{review.title}</h4><p>{review.notice_text}</p>
   <p>Recorded status: <strong>{review.status.replaceAll('_',' ')}</strong>. Last changed <time dateTime={review.status_updated_at}>{new Date(review.status_updated_at).toLocaleString()}</time>.</p>
   {stale&&<p data-testid="compliance-stale">This recorded status is older than the configured {workspace.policy.status_stale_days}-day freshness window. This is a display freshness warning, not evidence of new progress. Reload or contact support for confirmation.</p>}
   <div className="compliance-progress"><section><h4>Your action</h4>
    <ul>{review.requested_items.filter(item=>!['accepted','submitted_awaiting_review'].includes(item.submission_status)).map(item=><li key={item.item_id}>{item.label}: {primaryStatus(item)}{review.due_date&&` · Case deadline ${review.due_date}`}</li>)}</ul>
    {review.info_requests.filter(item=>item.status==='open').map(item=><article key={item.request_id}><p>{item.question}</p><p>{item.instructions||'Submission instructions are not recorded; contact support.'}</p><p>Request deadline: {item.due_date||'Not recorded'}.</p>
     {onAsk&&<button className="bank-chat-logout" disabled={busy||chatBusy} onClick={()=>onAsk('Please explain the selected recorded request for additional information and where I should respond.',{kind:'information_request',case_id:review.case_id,request_id:item.request_id})}>Explain this request</button>}
     {onSupport&&<button className="bank-chat-logout" disabled={busy||chatBusy} onClick={()=>onSupport({review_case_id:review.case_id,request_id:item.request_id})}>Get help with this request</button>}</article>)}
   </section><section><h4>With the bank</h4><ul>{review.requested_items.filter(item=>['accepted','submitted_awaiting_review'].includes(item.submission_status)).map(item=><li key={item.item_id}>{item.label}: {primaryStatus(item)}</li>)}</ul><p>Only recorded bank decisions are shown. A preliminary check never approves the review.</p></section></div>
   {onSupport&&<button className="bank-chat-logout" disabled={busy||chatBusy} onClick={()=>onSupport({review_case_id:review.case_id})}>Get help with this review</button>}
   <h4>Documents to upload</h4><p>Use fictional files only. Each row shows one recorded status; preliminary findings appear separately. Corrected uploads create new private versions.</p>
   <ul className="compliance-items">{review.requested_items.map(item=>{
    const current=workspace.current_documents[item.item_id],report=workspace.checks.find(report=>report.document_id===current),fresh=report&&checkIsFresh(report,now),rule=workspace.rules[item.item_id];
    return <li key={item.item_id} data-testid="compliance-item" data-item-id={item.item_id}>
     <h4>{item.label}</h4><p data-testid="compliance-item-status"><strong>{primaryStatus(item)}</strong></p><p>{item.instructions||'Instructions unavailable. Contact support rather than guessing.'}</p>
     {rule?<details><summary>Requirements from your notice</summary><p>{item.instructions||item.label}</p>{item.check_guidance&&<p>Approved checking guidance: {item.check_guidance}</p>}<p>Checking policy: results remain valid for {workspace.policy.validity_hours} hours; period coverage uses {workspace.policy.statement_period_mode.replaceAll('_',' ')} unless clarified by approved guidance.</p><p>Checks use your recorded notice and this item’s instructions. Labels, order and page layout can vary. Requested date coverage does not imply a page count. PDF and DOCX text, or readable PNG/JPEG scans with local OCR, are supported. Unclear evidence needs a clearer copy or bank support.</p><p>Extracted text is interpreted by the configured AI provider for this preliminary check. Use fictional files only. Authenticity, customer identity, handwriting and signature validity are not verified.</p></details>:<p>No assessment rules are configured. Contact support.</p>}
     {!closed&&!['accepted','submitted_awaiting_review'].includes(item.submission_status)&&<ChecklistPreparation key={`${item.item_id}:${item.client_marked_at}`} review={review} item={item} onSaved={refresh}/>}
     {report&&<div data-testid="precheck-result" data-precheck-status={fresh?report.status:'expired'}>
      <strong>PRELIMINARY check – not formal approval</strong><p>{!fresh?'This preliminary check has expired. Check the current upload again before submitting.':report.status==='no_issues'?'No issues found against the recorded notice requirements.':report.status==='issues_found'?'Please correct these notice requirement issues:':'Unable to assess reliably. No pass or fail is given.'}</p>
      <ul>{fresh&&report.findings.map((finding,index)=><li key={index}>{findingText(finding,rule)}</li>)}</ul>
      <p>Checked {new Date(report.checked_at).toLocaleString()}. Valid until {report.expires_at?new Date(report.expires_at).toLocaleString():'the recorded expiry'}. Authenticity, handwriting and signature verification are not performed.</p>
      {onAsk&&<button className="bank-chat-logout" disabled={busy||chatBusy} onClick={()=>onAsk('Explain the selected preliminary document check findings and how to fix them.',{kind:'document',case_id:review.case_id,item_id:item.item_id,document_id:current,check_id:report.check_id})}>Explain findings in chat</button>}
     </div>}
     {onSupport&&<button className="bank-chat-logout" disabled={busy||chatBusy} onClick={()=>onSupport({review_case_id:review.case_id,item_id:item.item_id})}>Get help with this document</button>}
     {current&&!closed&&item.submission_status!=='accepted'&&<button className="bank-chat-logout" data-testid="check-current-upload" disabled={busy||chatBusy||!rule} onClick={()=>checkAndSubmit(current,true)}>Check and submit current upload</button>}
     {!closed&&item.submission_status!=='accepted'&&<DocumentUpload key={current||item.item_id} review={review} itemId={item.item_id} onRegistered={checkAndSubmit} disabled={busy||chatBusy}/>}
    </li>;
   })}</ul>
   {displayedReceipt&&<p role="status" data-testid="submission-receipt">Submitted – awaiting review. Receipt: <code>{displayedReceipt}</code>. This is not bank acceptance.</p>}
   <p>Retrieved {new Date(workspace.as_of).toLocaleString()}. Case deadline: {review.due_date||'Not recorded'}.</p>
  </>}
 </section>;
}
