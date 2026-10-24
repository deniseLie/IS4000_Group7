import {useEffect,useState} from 'react';
import Icon from './Icon';
import Stub from './Stub';
import ChecklistPreparation from './ChecklistPreparation';
import DocumentRecords from './DocumentRecords';
import {supabase} from '../lib/supabase';
import {loadSections} from '../lib/customerData';

function label(value) {const text=value.replaceAll('_',' ');return text[0].toUpperCase()+text.slice(1);}
const NEEDS_ACTION=['not_submitted','rejected','unknown'];
const BADGES={accepted:'active',rejected:'revoked',not_submitted:'warning',unknown:'warning'};

export default function CompliancePage({onSupport,onAsk}) {
  const [records,setRecords]=useState(null);
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  useEffect(()=>{
    const controller=new AbortController();setBusy(true);
    async function load() {
      try {
        const {data,error:sessionError}=await supabase.auth.getSession();
        if(sessionError)throw sessionError;
        if(!data.session)throw new Error('Please sign in again to view your compliance review.');
        const result=await loadSections(data.session.access_token,['compliance','documents'],controller.signal);
        if(!controller.signal.aborted)setRecords(result);
      } catch(failure) {if(failure.name!=='AbortError'&&!controller.signal.aborted)setError(failure.message);}
      finally {if(!controller.signal.aborted)setBusy(false);}
    }
    load();return ()=>controller.abort();
  },[]);
  const review=records?.compliance.data,items=review?.requested_items||[];
  const prepared=items.filter(item=>item.client_mark==='prepared').length,accepted=items.filter(item=>item.submission_status==='accepted').length;
  return <section className="app-page-body compliance-page" aria-label="Compliance review" data-testid="compliance-page">
    {busy&&<p role="status">Loading your recorded review…</p>}
    {error&&<p role="alert">{error}</p>}
    {records&&!review&&<><div className="page-header"><div><span className="eyebrow">Compliance review</span><h1>No open review</h1><p>No client-visible compliance review is recorded for your demo profile.</p></div></div></>}
    {review&&<>
      <div className="case-meta"><span className="status-badge">{label(review.status)}</span><span>Notice dated {review.notice_date}</span>{review.due_date&&<span className="status-badge warning"><Icon name="calendar"/>Due {review.due_date}</span>}</div>
      <div className="page-header"><div><h1>{review.title}</h1><p>{review.notice_text}</p></div><span className="live-pill"><span className="dot"/>Synthetic case</span></div>
      <div className="case-card">
        <div className="case-progress">
          <div className="progress-label"><strong>Your preparation</strong><span>{prepared} of {items.length} marked prepared</span></div>
          <div className="progress-track" role="progressbar" aria-label="Items you marked prepared" aria-valuemin={0} aria-valuemax={items.length} aria-valuenow={prepared}><span style={{width:`${items.length?prepared/items.length*100:0}%`}}/></div>
          <div className="progress-label"><span>Accepted by the bank: {accepted} of {items.length}</span><span>Your marks do not submit documents</span></div>
        </div>
        <Stub title="AI assistant summary"><p>A plain-language summary of this case appears here in the design. AI prechecks are not connected yet, so no summary is generated.</p></Stub>
      </div>
      <div className="compliance-grid">
        <div className="relationship-column">
          <div className="column-label"><span>Required submissions & document status</span><span>{items.length} requested items</span></div>
          {items.length===0&&<p className="page-empty">No items are requested for this review.</p>}
          {items.map(item=>{const action=NEEDS_ACTION.includes(item.submission_status);return <article key={item.item_id} className={`requirement-card ${action?'priority':''}`} data-checklist-item-id={item.item_id}>
            <div className="requirement-head"><span className="transition-icon"><Icon name="document"/></span><div>
              <h3>{item.label} <span className={`status-badge ${BADGES[item.submission_status]||''}`}>{action&&item.submission_status!=='rejected'?'Action required':label(item.submission_status)}</span></h3>
              {item.instructions&&<p>{item.instructions}</p>}
              <p className="requirement-meta">Your mark: {label(item.client_mark).toLowerCase()}{item.client_marked_at&&<> · Marked <time dateTime={item.client_marked_at}>{new Date(item.client_marked_at).toLocaleDateString()}</time></>}</p>
            </div></div>
            {action&&<Stub title="Plain-language guidance (AI)"><dl className="guidance-list"><dt>What is this?</dt><dd>—</dd><dt>Why was this flagged?</dt><dd>—</dd><dt>What to do next</dt><dd>—</dd></dl></Stub>}
            <ChecklistPreparation review={review} item={item}/>
          </article>;})}
          <div className="info-card"><Icon name="info"/><div><strong>Data integrity & review trail</strong><p>Each section on this page is a separate audited read, recorded as of <time dateTime={records.compliance.as_of}>{new Date(records.compliance.as_of).toLocaleString()}</time>. Reload the page for current status.</p></div></div>
        </div>
        <div className="relationship-column">
          <div className="advisor-card documents-card"><DocumentRecords snapshot={records.documents} review={review}/></div>
          <Stub title="AI document checker"><p>The design shows an instant pre-check of uploaded documents against bank requirements. Only the counts-only technical check under each document is connected; it does not judge bank requirements.</p></Stub>
          <section className="advisor-card faq-card" aria-label="Plain English FAQ"><h2 className="section-title">Plain English FAQ</h2>
            <details><summary>What does “prepared” mean?</summary><p>It is your own note that an item is ready. It does not upload or submit anything, and it does not change the bank’s review status.</p></details>
            <details><summary>Does uploading a document complete an item?</summary><p>No. Uploading records a private file. Bank acceptance and checklist status are separate decisions.</p></details>
            <details><summary>Can someone help me with this review?</summary><p>You can prepare a short support summary for bank staff in this demo. No person will reply.</p><button className="bank-chat-logout" onClick={onSupport}><Icon name="support"/>Talk to a person</button></details>
          </section>
          <button className="guidance-card" onClick={()=>onAsk('What does my compliance review need from me?')}><span className="transition-icon"><Icon name="spark"/></span><span><strong>Ask the banking assistant</strong><small>Get general guidance about this review in chat.</small></span></button>
        </div>
      </div>
    </>}
    <p className="page-note"><Icon name="info"/>IS4000 coursework demo · Synthetic case data · No real bank review</p>
  </section>;
}
