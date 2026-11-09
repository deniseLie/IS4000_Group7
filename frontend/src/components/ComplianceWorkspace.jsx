import {useEffect,useRef,useState} from 'react';
import {supabase} from '../lib/supabase';
import {checkIsFresh,findingText,loadTemplates,loadWorkspace,primaryStatus,reviewNextStep,runPrecheck,submitDocument,templateFor} from '../lib/compliance';
import ChecklistPreparation from './ChecklistPreparation';
import Icon from './Icon';
import {formatDate,formatDateTime} from '../lib/format';
import {cacheOwner,cached,remember} from '../lib/cache';
import DocumentUpload from './DocumentUpload';
import BankRequest from './BankRequest';
// CR5: every preliminary result carries this label; only the bank's review decides.
export const PRECHECK_LABEL='Preliminary Check – Not Formal Compliance Approval';

// What each kind of document must show, with a made-up example (GET /documents/templates; public, synthetic).
function DocumentGuide({template}){
 if(!template)return null;
 return <div className="cw-guide" data-testid="document-guide"><strong>A good file shows</strong>
  <ul>{template.must_show.map(line=><li key={line}>{line}</li>)}</ul>
  <details className="cw-more"><summary>See an example</summary><div className="cw-example" aria-label={`Example: ${template.title}`}>{template.example.map(line=><p key={line}>{line}</p>)}</div></details>
  {template.tips?.length>0&&<p className="rm-small">{template.tips.join(' ')}</p>}</div>;
}

const sentence=value=>{const text=value.replaceAll('_',' ');return text[0].toUpperCase()+text.slice(1);};

export default function ComplianceWorkspace({onPage=false,onAsk,chatBusy=false,onBusyChange,onSupport,onReviewLoaded,submissionReceipt=null,onSubmitted}){
 // A return visit shows the last loaded review at once (actions stay disabled while busy); only a fresh read sets the server clock.
 const [workspace,setWorkspace]=useState(()=>cached('compliance')),[error,setError]=useState(''),[busy,setBusy]=useState(false),[receipt,setReceipt]=useState(null),[now,setNow]=useState(Date.now());
 const [templates,setTemplates]=useState([]),[templateError,setTemplateError]=useState('');
 const mounted=useRef(false),active=useRef(null),serverClock=useRef(null),owner=useRef(cacheOwner());
 function snapshot(result){serverClock.current={server:Date.parse(result.as_of),local:performance.now()};setNow(serverClock.current.server);setWorkspace(remember('compliance',result,owner.current));onReviewLoaded?.(result.review?.case_id??null);}
 async function token(){const {data,error}=await supabase.auth.getSession();if(error)throw error;if(!data.session)throw new Error('Sign in again to view your compliance review.');return data.session.access_token;}
 async function refresh(){
  active.current?.abort();const controller=new AbortController();active.current=controller;setBusy(true);setError('');
  try{const result=await loadWorkspace(await token(),controller.signal);if(mounted.current&&!controller.signal.aborted)snapshot(result);}
  catch(failure){if(mounted.current&&!controller.signal.aborted){setError(failure.message);setWorkspace(null);}}
  finally{if(mounted.current&&!controller.signal.aborted)setBusy(false);}
 }
 useEffect(()=>{const controller=new AbortController();loadTemplates(controller.signal).then(setTemplates).catch(failure=>{if(!controller.signal.aborted)setTemplateError(failure.message);});return ()=>controller.abort();},[]);
 useEffect(()=>{onBusyChange?.(busy);return ()=>onBusyChange?.(false);},[busy,onBusyChange]);
 useEffect(()=>{mounted.current=true;refresh();const timer=setInterval(()=>{const base=serverClock.current;if(base)setNow(base.server+performance.now()-base.local);},1000);return ()=>{mounted.current=false;active.current?.abort();clearInterval(timer);};},[]);
 async function checkAndSubmit(documentId,recheck=false){
  setBusy(true);setError('');setReceipt(null);
  try{
   const access=await token();
   // Registration has already saved the new file. Show it even if the next stage fails.
   const uploaded=await loadWorkspace(access);if(mounted.current)snapshot(uploaded);
   const report=await runPrecheck(documentId,access,undefined,recheck);
   const checked=await loadWorkspace(access);if(mounted.current)snapshot(checked);
   if(report.status==='no_issues'){
    const result=await submitDocument(documentId,access);
    if(result.receipt?.outcome!=='succeeded')throw new Error('The item or file changed. Your document was not submitted. Reload before retrying.');
    if(mounted.current){setReceipt(result.receipt.receipt_id);onSubmitted?.({case_id:report.case_id,receipt_id:result.receipt.receipt_id});}
   }
   // Stay where the client is (Compliance page or the review under the chat); the chat is used only when they ask.
   const result=await loadWorkspace(access);if(mounted.current)snapshot(result);
  }catch(failure){if(mounted.current)setError(failure.message+' Refresh to confirm the recorded status before retrying.');}
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
 const toSend=pending.filter(item=>item.submission_status!=='unknown'),unknown=pending.filter(item=>item.submission_status==='unknown');
 const nothing=!closed&&!items.length&&!review?.info_requests?.some(request=>request.status==='open');  // no document or question is waiting
 const markTone=item=>item.submission_status==='rejected'?'bad':item.client_mark==='prepared'?'ok':'todo';
 const icon=item=>/address/i.test(item.label)?'pin':/statement/i.test(item.label)?'document':/tax/i.test(item.label)?'bank':'card';
 const help=selection=>onSupport&&<button className="bank-chat-logout" data-testid="compliance-support" disabled={busy||chatBusy} onClick={()=>onSupport(selection)}><Icon name="support"/>{selection.request_id?'Get help with this request':selection.item_id?'Get help with this document':'Get help with this review'}</button>;
 return <section className={`compliance-workspace cw ${onPage?'cw-page':''}`} data-testid="compliance-workspace" aria-label="Your periodic review">
  {onPage&&<header className="page-header"><div><span className="eyebrow">Your account</span><h1>Periodic review</h1><p>Every few years we check that your details and documents are up to date. This is routine and doesn’t mean anything is wrong.</p></div>
   <div className="page-header-actions">{review?.due_date&&!closed&&<span className="cw-due">Please send by {formatDate(review.due_date)}</span>}<button className="icon-button" aria-label="Refresh" title="Refresh" disabled={busy} onClick={refresh}><Icon name="refresh"/></button></div></header>}
  {!onPage&&<div className="cw-top"><h3 className="cw-chat-title">Your periodic review</h3><button className="bank-chat-logout" disabled={busy} onClick={refresh}><Icon name="refresh"/>Refresh</button></div>}
  {busy&&<p className="cw-busy loading" role="status">Checking your periodic review…</p>}
  {busy&&!workspace&&<div className="loading-list" aria-hidden="true"><span className="skeleton-block tall"/><span className="skeleton-card large"/><span className="skeleton-card large"/></div>}
  {error&&<p role="alert" data-testid="compliance-workspace-error">{error}</p>}
  {workspace&&!review&&<div className="cw-none" data-testid="compliance-none"><span><Icon name="check"/></span><div><h4>No review needed</h4><p>There is nothing for you to do right now. If the bank needs documents or information, you will see it here and in your Inbox.</p></div></div>}
  {review&&<>
   {!nothing&&<ol className="cw-steps" aria-label="Review progress">
    {[['Send your documents',closed?'Done':`${finished} of ${items.length} sent`],['The bank checks them',closed?'Done':pending.length===0&&items.length?'In progress':'After you send them'],['Done',closed?sentence(review.status):'We’ll tell you in your Inbox']].map(([label,sub],i)=>{
     const at=closed?3:pending.length===0&&items.length?1:0;
     return <li key={label} className={i<at?'done':i===at?'current':''} aria-current={i===at?'step':undefined}><span className="cw-step-mark">{i<at?'✓':i+1}</span><span><strong>{label}</strong><small>{sub}</small></span></li>;})}</ol>}
   <div className="cw-layout"><div className="cw-main">
   {/* The bank's questions (0080): open ones first with a reply box, then answered and closed ones. */}
   {[...review.info_requests].sort((a,b)=>({open:0,responded:1,closed:2}[a.status]-{open:0,responded:1,closed:2}[b.status])).map(item=><BankRequest key={item.request_id}
    review={review} request={item} disabled={busy||chatBusy} onAnswered={refresh}
    onAsk={onAsk&&(()=>onAsk('Please explain the selected recorded request for additional information and how I should answer it.',{kind:'information_request',case_id:review.case_id,request_id:item.request_id}))}
    help={help({review_case_id:review.case_id,request_id:item.request_id})}/>)}
   {nothing&&<div className="cw-none cw-nothing" data-testid="compliance-nothing"><span><Icon name="check"/></span><div><h4>Nothing needed from you</h4>
    <p>Your periodic review has no documents or questions waiting for you. If the bank needs anything, you will see it here and in your Inbox.</p></div></div>}
   {!nothing&&<h2 className="cw-h2">What we need from you</h2>}
   {templateError&&<p role="alert" data-testid="document-guide-error">{templateError} Read the official notice or get help with your document before uploading.</p>}
   <ul className="compliance-items">{[...items].sort((a,b)=>(DONE.includes(a.submission_status)?1:0)-(DONE.includes(b.submission_status)?1:0)).map(item=>{
    const current=workspace.current_documents[item.item_id],report=workspace.checks.find(report=>report.document_id===current),fresh=report&&checkIsFresh(report,now),rule=workspace.rules[item.item_id];
    const open=!closed&&item.submission_status!=='accepted';
    const submitted=item.submission_status==='submitted_awaiting_review',accepted=item.submission_status==='accepted';
    const problem=report&&report.status!=='no_issues';
    const template=templateFor(item,templates);
    const file=current&&workspace.documents.find(document=>document.document_id===current)?.file_name;
    const upload=<DocumentUpload key={current||item.item_id} review={review} itemId={item.item_id} onRegistered={checkAndSubmit} disabled={busy||chatBusy}/>;
    const status=<p data-testid="compliance-item-status" className={`cw-pill ${DONE.includes(item.submission_status)?tone(item):markTone(item)}`}><strong>{primaryStatus(item)}</strong></p>;
    if(accepted||submitted)return <li key={item.item_id} data-testid="compliance-item" data-item-id={item.item_id} className={`cw-item cw-item-done ${tone(item)}`}>
     <div className="cw-done-row"><span className="cw-done-mark" aria-hidden="true"><Icon name="check"/></span>
      <div className="cw-done-text"><strong>{item.label}</strong>
       {accepted?<small data-testid="item-accepted">Accepted by the bank.</small>
        :<small data-testid="item-sent">{file?`${file} · `:''}sent {formatDate(item.updated_at)} · the bank is checking it</small>}</div>{status}</div>
     {submitted&&open&&<details className="cw-more"><summary data-testid="replace-document">Need to send a different file?</summary>
      <p className="rm-small">The earlier file stays with the bank. A new one is checked before it can be sent.</p>{upload}</details>}
    </li>;
    return <li key={item.item_id} data-testid="compliance-item" data-item-id={item.item_id} className={`cw-item ${tone(item)}`}>
     <div className="cw-item-head"><h4>{item.label}</h4>{status}</div>
     <p className="cw-reason">{item.instructions||'Use Get help to ask what to send.'}</p>
     {template?.must_show?.length>0&&<div className="cw-good" data-testid="document-guide"><span className="eyebrow">A good file shows</span><p>{template.must_show.join(' · ')}</p></div>}
     {current&&<div data-testid="precheck-result" data-precheck-status={report?.status||'not_checked'} className={`cw-result ${!report?'none':problem?'issues':'ok'}`}>
      <span className="vh" data-testid="precheck-label">{PRECHECK_LABEL}</span>
      {report?<>
       <strong>{problem?report.status==='issues_found'?`We checked ${file}: ${report.findings.length===1?'one thing is':'a few things are'} missing`:`We couldn’t check ${file}`:`We checked ${file}: it looks complete`}</strong>
       {problem&&<ul>{report.findings.map((finding,index)=><li key={index}>{findingText(finding,rule)}</li>)}</ul>}
       {!fresh&&<p className="rm-small">Checked {formatDateTime(report.checked_at)}: check it again before sending.</p>}
       {problem&&fresh&&onAsk&&<button className="link-button" data-testid="explain-document-check" disabled={busy||chatBusy} onClick={()=>onAsk('Explain the selected preliminary document check findings and how to fix them.',{kind:'document',case_id:review.case_id,item_id:item.item_id,document_id:current,check_id:report.check_id})}>Ask the assistant what this means</button>}
      </>:<strong>{file}: not checked yet</strong>}
      <span className="rm-small cw-result-note">A quick check of the file, not the bank’s decision.</span>
     </div>}
     {open&&<div className="cw-actions-row">
      {upload}
      {current&&rule&&<button className="bank-chat-logout" data-testid="check-current-upload" disabled={busy||chatBusy} onClick={()=>checkAndSubmit(current,true)}>{problem?'Check my last file again':'Send my last file to the bank'}</button>}
      {template?.example?.length>0&&<details className="cw-more cw-example-toggle"><summary>See an example</summary><div className="cw-example" aria-label={`Example: ${template.title}`}>{template.example.map(line=><p key={line}>{line}</p>)}</div>
       {template.tips?.length>0&&<p className="rm-small">{template.tips.join(' ')}</p>}</details>}
     </div>}
     {open&&<details className="cw-more cw-note"><summary data-testid="checklist-toggle">Not ready to send yet? Mark it for yourself</summary>
      <ChecklistPreparation key={`${item.item_id}:${item.client_marked_at}`} review={review} item={item} onSaved={refresh}/></details>}
    </li>;
   })}</ul>
   {!nothing&&<p className="rm-small cw-foot">When you upload, we check the file straight away so you know it’s complete. Then it goes to the bank, which reviews it. Use demo files only (PDF works best, up to 10 MB).</p>}
   {displayedReceipt&&<p role="status" data-testid="submission-receipt" className="cw-receipt">Sent. You’ll see the bank’s decision here and in your Inbox.</p>}
   </div>
   <aside className="cw-side">
    <section className="cw-card cw-side-card" aria-label="Your progress"><span className="eyebrow">Your progress</span>
     <div className={`cw-bar ${nothing?'full':''}`}><span style={{width:`${nothing?100:items.length?finished/items.length*100:0}%`}}/></div>
     <p>{nothing?'Nothing to send':`${finished} of ${items.length} document${items.length===1?'':'s'} sent${pending.length?` · ${pending.length} to go`:''}`}</p>
     <p className="cw-meta"><span className={`cw-chip ${closed||!reviewNextStep(review).needsAttention?'ok':''}`} data-testid="compliance-next-step">{closed?sentence(review.status):reviewNextStep(review).label}</span>{review.due_date&&<span>Due {formatDate(review.due_date)}</span>}</p>
     {stale&&<p className="cw-warn" data-testid="compliance-stale">This status is more than {workspace.policy.status_stale_days} days old. Refresh, or ask your relationship manager to confirm progress.</p>}</section>
    <section className="cw-card cw-side-card" aria-label="Need help?"><span className="eyebrow">Need help?</span>
     <p>Not sure which file to send? Ask the assistant, or your relationship manager can help.</p>
     <div className="cw-side-actions">{onAsk&&<button className="bank-chat-logout" disabled={busy||chatBusy} onClick={()=>onAsk('What does my periodic review still need, and what should each document show?')}>Ask the assistant</button>}
      {help({review_case_id:review.case_id})}</div></section>
    <details className="cw-card cw-side-card cw-notice"><summary>Official notice and reference</summary>
     <p className="rm-small">Reference #{review.case_id.replaceAll('-','').slice(-8).toUpperCase()} · updated <time dateTime={review.status_updated_at}>{formatDateTime(review.status_updated_at)}</time></p>
     <p className="rm-small" data-testid="compliance-bank-case-status">The bank’s case status: {sentence(review.status)}.</p>
     <p>{review.notice_text}</p></details>
   </aside></div>
  </>}
 </section>;
}
